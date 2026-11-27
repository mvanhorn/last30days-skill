"""Juejin source: paged search, window and relevance gating, comments."""

import datetime

from lib import normalize, pipeline, juejin

UTC = datetime.timezone.utc


def _ts(day, hour=8):
    return str(int(datetime.datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=UTC).timestamp()))


def _row(article_id, title, *, brief="", day="2026-11-20", likes=0, comments=0, saves=0, views=10,
         author="alice", author_id="100", tags=("AI编程",)):
    return {
        "result_type": 2,
        "result_model": {
            "article_id": str(article_id),
            "article_info": {
                "article_id": str(article_id),
                "title": title,
                "brief_content": brief or f"About {title}",
                "ctime": _ts(day),
                "digg_count": likes,
                "comment_count": comments,
                "collect_count": saves,
                "view_count": views,
                "user_id": author_id,
            },
            "author_user_info": {"user_name": author, "user_id": author_id},
            "tags": [{"tag_name": t} for t in tags],
            "category": {"category_name": "人工智能"},
        },
    }


def _comment(user, text, *, likes=0, user_id=None, bot_id="0"):
    return {
        "comment_info": {
            "comment_content": text,
            "digg_count": likes,
            "user_id": user_id or f"u-{user}",
            "bot_id": bot_id,
            "ctime": 1795667778,
        },
        "user_info": {"user_name": user, "user_id": user_id or f"u-{user}"},
    }


class FakeApi:
    def __init__(self, pages=None, comments=None, fail=(), search_body=None):
        self.pages = pages or [[]]
        self.comments = comments or {}
        self.fail = set(fail)
        self.search_body = search_body
        self.calls = []

    def __call__(self, method, url, headers=None, json_data=None, **kwargs):
        assert method == "POST"
        self.calls.append((url, json_data, kwargs))
        if url == juejin.SEARCH_URL:
            page = int(json_data["cursor"])
            if page in self.fail or "search" in self.fail:
                raise OSError(f"page {page + 1} down")
            if self.search_body is not None:
                return self.search_body
            rows = self.pages[page] if page < len(self.pages) else []
            has_more = page + 1 < len(self.pages)
            return {"err_no": 0, "err_msg": "success", "data": rows, "has_more": has_more, "cursor": str(page + 1)}
        if url == juejin.COMMENTS_URL:
            if "comments" in self.fail:
                raise OSError("comments down")
            return {"err_no": 0, "data": self.comments.get(json_data["item_id"], [])}
        raise AssertionError(f"unexpected URL {url}")


def _install(monkeypatch, api):
    monkeypatch.setattr(juejin.http, "request", api)
    return api


def _search_calls(api):
    return [body for url, body, _ in api.calls if url == juejin.SEARCH_URL]


def _source_items(rows):
    parsed = juejin.parse_juejin_response({"articles": [r["result_model"] for r in rows]}, query="claude code")
    return normalize.normalize_source_items("juejin", parsed, "2026-10-28", "2026-11-27")


def _diagnose_sources(argv, config=None):
    import json

    from tests.test_diagnose_compat import FAKE_KEYLESS_CONFIG, _run_cli

    rc, out = _run_cli(["--diagnose", *argv], {**FAKE_KEYLESS_CONFIG, **(config or {})})
    assert rc == 0
    return json.loads(out)["available_sources"]


def test_juejin_is_off_by_default():
    assert "juejin" not in pipeline.available_sources({}, None, x_pending=False)


def test_include_sources_and_search_each_activate_it():
    assert "juejin" in pipeline.available_sources({"INCLUDE_SOURCES": "juejin"}, None, x_pending=False)
    assert "juejin" in pipeline.available_sources({}, ["juejin"], x_pending=False)


def test_cli_activation_and_exclusion_through_diagnose():
    assert "juejin" not in _diagnose_sources([])
    assert "juejin" in _diagnose_sources(["--search", "reddit,juejin"])
    assert "juejin" in _diagnose_sources([], {"INCLUDE_SOURCES": "juejin"})
    assert "juejin" not in _diagnose_sources([], {"INCLUDE_SOURCES": "juejin", "EXCLUDE_SOURCES": "juejin"})


def test_search_sends_newest_first_query_without_quotes(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    juejin.search_juejin("claude code", "2026-10-28", "2026-11-27", search_query='"claude code"')
    [body] = _search_calls(api)
    assert body["key_word"] == "claude code"
    assert body["sort_type"] == 1
    assert body["id_type"] == 2
    assert body["cursor"] == "0"


def test_pages_follow_the_cursor_up_to_the_depth_cap(monkeypatch):
    pages = [[_row(n * 10 + i, f"Claude Code {n}{i}") for i in range(2)] for n in range(8)]
    api = _install(monkeypatch, FakeApi(pages=pages))
    juejin.search_juejin("claude code", "2026-10-28", "2026-11-27", depth="default")
    assert [b["cursor"] for b in _search_calls(api)] == ["0", "1", "2"]
    api.calls.clear()
    juejin.search_juejin("claude code", "2026-10-28", "2026-11-27", depth="quick")
    assert [b["cursor"] for b in _search_calls(api)] == ["0"]


def test_paging_stops_when_a_whole_page_predates_the_window(monkeypatch):
    pages = [
        [_row(1, "Claude Code one", day="2026-11-20"), _row(2, "Claude Code two", day="2026-09-01")],
        [_row(3, "Claude Code three", day="2026-08-01"), _row(4, "Claude Code four", day="2026-07-01")],
        [_row(5, "Claude Code five", day="2026-11-21")],
    ]
    api = _install(monkeypatch, FakeApi(pages=pages))
    result = juejin.search_juejin("claude code", "2026-10-28", "2026-11-27", depth="deep")
    assert [b["cursor"] for b in _search_calls(api)] == ["0", "1"]
    assert [r["article_id"] for r in result["articles"]] == ["1"]


def test_window_relevance_dedupe_and_engagement_ranking(monkeypatch):
    pages = [
        [
            _row(1, "Claude Code 上手", views=40),
            _row(2, "Claude Code 实战", likes=12, comments=3, views=900),
            _row(3, "周末去爬山", brief="风景很好"),
            _row(4, "Claude Code 旧文", day="2026-09-01", likes=500),
            _row(5, "Claude Code 来自未来", day="2026-12-25"),
        ],
        [_row(2, "Claude Code 实战", likes=12, comments=3, views=900)],
    ]
    _install(monkeypatch, FakeApi(pages=pages))
    result = juejin.search_juejin("claude code", "2026-10-28", "2026-11-27")
    assert [r["article_id"] for r in result["articles"]] == ["2", "1"]
    assert "error" not in result


def test_keep_limit_caps_results_by_depth(monkeypatch):
    rows = [_row(n, f"Claude Code {n}", likes=n) for n in range(15)]
    _install(monkeypatch, FakeApi(pages=[rows]))
    result = juejin.search_juejin("claude code", "2026-10-28", "2026-11-27", depth="quick")
    assert len(result["articles"]) == juejin.KEEP_LIMIT["quick"]
    assert result["articles"][0]["article_id"] == "14"


def test_chinese_topics_pass_relevance(monkeypatch):
    _install(monkeypatch, FakeApi(pages=[[_row(1, "鸿蒙原生应用开发实践", brief="从零开发一个鸿蒙应用")]]))
    result = juejin.search_juejin("鸿蒙开发", "2026-10-28", "2026-11-27")
    assert [r["article_id"] for r in result["articles"]] == ["1"]


def test_first_page_failure_is_an_error(monkeypatch):
    _install(monkeypatch, FakeApi(fail={"search"}))
    result = juejin.search_juejin("claude code", "2026-10-28", "2026-11-27")
    assert result["articles"] == []
    assert "page 1 down" in result["error"]


def test_api_error_code_is_an_error_not_an_empty_result(monkeypatch):
    _install(monkeypatch, FakeApi(search_body={"err_no": 2, "err_msg": "参数错误"}))
    result = juejin.search_juejin("claude code", "2026-10-28", "2026-11-27")
    assert result["articles"] == []
    assert "参数错误" in result["error"]


def test_later_page_failure_keeps_the_pages_fetched(monkeypatch):
    pages = [[_row(1, "Claude Code one")], [_row(2, "Claude Code two")], [_row(3, "Claude Code three")]]
    _install(monkeypatch, FakeApi(pages=pages, fail={1}))
    result = juejin.search_juejin("claude code", "2026-10-28", "2026-11-27")
    assert [r["article_id"] for r in result["articles"]] == ["1"]
    assert "error" not in result


def test_empty_query_makes_no_request(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    assert juejin.search_juejin("", "2026-10-28", "2026-11-27") == {"articles": []}
    assert api.calls == []


def test_requests_carry_a_total_deadline(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    juejin.search_juejin("claude code", "2026-10-28", "2026-11-27")
    _url, _body, kwargs = api.calls[0]
    assert kwargs["deadline_monotonic"] is not None


def test_parse_maps_article_fields():
    long_brief = "claude code " * 100
    [item] = juejin.parse_juejin_response(
        {"articles": [_row(7607, "Claude Code &amp; Codex", brief=long_brief, likes=5, comments=2, saves=3,
                           views=400, author="bob", author_id="42")["result_model"]]},
        query="claude code",
    )
    assert item["id"] == "7607"
    assert item["url"] == "https://juejin.cn/post/7607"
    assert item["title"] == "Claude Code & Codex"
    assert item["author"] == "bob"
    assert item["date"] == "2026-11-20"
    assert item["engagement"] == {"likes": 5, "comments": 2, "saves": 3, "views": 400}
    assert item["text"].endswith("...") and len(item["text"]) == juejin.TEXT_LIMIT + 3
    assert item["metadata"] == {"tags": ["AI编程"], "category": "人工智能", "author_id": "42"}
    assert 0 < item["relevance"] <= 1


def test_normalized_item_uses_juejin_container():
    [item] = _source_items([_row(1, "Claude Code 体验", brief="claude code 很好用")])
    assert item.source == "juejin"
    assert item.container == "Juejin"
    assert item.published_at == "2026-11-20"
    assert item.date_confidence == "high"
    assert item.snippet == "claude code 很好用"


def test_comments_rank_by_likes_and_drop_bots_and_the_author(monkeypatch):
    _install(monkeypatch, FakeApi(comments={"1": [
        _comment("carol", "写得很清楚", likes=1),
        _comment("alice", "作者补充：已更新", likes=9, user_id="100"),
        _comment("helper", "AI 总结：本文介绍了", likes=5, bot_id="7"),
        _comment("dave", "踩过同样的坑", likes=4),
        _comment("erin", "   "),
    ]}))
    comments = juejin._fetch_comments("1", "100")
    assert [(c["author"], c["text"], c["likes"]) for c in comments] == [
        ("dave", "踩过同样的坑", 4),
        ("carol", "写得很清楚", 1),
    ]
    assert comments[0]["date"] == "2026-11-26"


def test_enrichment_targets_top_items_with_comments(monkeypatch):
    api = _install(monkeypatch, FakeApi(comments={
        "1": [_comment("bob", "claude code 好用", likes=3)],
        "3": [_comment("dave", "不错")],
    }))
    items = _source_items([
        _row(1, "Claude Code one", comments=2),
        _row(2, "Claude Code two", comments=0),
        _row(3, "Claude Code three", comments=1),
        _row(4, "Claude Code four", comments=1),
        _row(5, "Claude Code five", comments=1),
    ])
    juejin.enrich_source_items(items)
    fetched = sorted(body["item_id"] for url, body, _ in api.calls if url == juejin.COMMENTS_URL)
    assert fetched == ["1", "3", "4"]
    assert [(c["excerpt"], c["score"]) for c in items[0].metadata["top_comments"]] == [("claude code 好用", 3)]
    assert not items[1].metadata.get("top_comments")


def test_failed_comment_fetch_keeps_the_item(monkeypatch):
    _install(monkeypatch, FakeApi(fail={"comments"}))
    items = _source_items([_row(1, "Claude Code one", comments=4)])
    juejin.enrich_source_items(items)
    assert not items[0].metadata.get("top_comments")


def test_enrichment_skips_when_the_deadline_has_passed(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    items = _source_items([_row(1, "Claude Code one", comments=2)])
    juejin.enrich_source_items(items, deadline=0.0)
    assert api.calls == []


def test_enrichment_stops_waiting_at_its_time_limit(monkeypatch):
    import threading
    import time

    release = threading.Event()

    def stuck(article_id, author_id):
        release.wait(5)
        return [{"author": "a", "text": "late", "likes": 1}]

    monkeypatch.setattr(juejin, "_fetch_comments", stuck)
    monkeypatch.setattr(juejin, "ENRICH_WAIT_SECONDS", 0.2)
    items = _source_items([_row(1, "Claude Code one", comments=2)])
    started = time.monotonic()
    juejin.enrich_source_items(items)
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 2
    assert not items[0].metadata.get("top_comments")


def test_engagement_score_counts_views_on_a_like_free_article():
    from lib import signals

    [quiet] = _source_items([_row(1, "Claude Code one", views=35)])
    [busy] = _source_items([_row(2, "Claude Code two", likes=8, comments=3, saves=4, views=1200)])
    assert 15 <= signals.engagement_score(quiet) <= 30
    assert 80 <= signals.engagement_score(busy) <= 95


def test_doctor_reports_juejin_as_opt_in_until_enabled():
    from lib import doctor

    assert doctor._juejin_record({})["status"] == "opt-in"
    assert doctor._juejin_record({"INCLUDE_SOURCES": "juejin"})["status"] == "ok"
    assert "juejin" not in doctor._probeable_sources()


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


def test_activated_juejin_joins_a_plan_that_omits_it():
    plan = _plan(["hackernews"])
    pipeline._ensure_opt_in_source_in_plan(plan, ["hackernews", "juejin"], "juejin")
    assert "juejin" in plan.subqueries[0].sources
    assert "juejin" in plan.source_weights


def test_retrieve_stream_runs_search_and_parse(monkeypatch):
    from lib import schema

    _install(monkeypatch, FakeApi(pages=[[_row(1, "Claude Code 体验", brief="claude code 很好用")]]))
    subquery = schema.SubQuery(label="primary", search_query="claude code",
                               ranking_query="q", sources=["juejin"], weight=1.0)
    items, _artifact = pipeline._retrieve_stream_impl(
        topic="claude code", subquery=subquery, source="juejin", config={},
        depth="default", date_range=("2026-10-28", "2026-11-27"),
        runtime=None, mock=False,
    )
    assert [item["url"] for item in items] == ["https://juejin.cn/post/1"]
