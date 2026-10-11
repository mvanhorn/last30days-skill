"""dev.to source: tag and search retrieval, relevance gating, comments."""

import urllib.parse

import pytest

from lib import devto, normalize, pipeline


@pytest.fixture(autouse=True)
def _fresh_cache():
    devto.clear_cache()
    yield
    devto.clear_cache()


def _article(article_id, title, *, tags=("ai",), published="2026-10-05T12:00:00Z", reactions=5, comments=2):
    return {
        "id": article_id,
        "title": title,
        "description": f"About {title}",
        "url": f"https://dev.to/someone/post-{article_id}",
        "published_at": published,
        "public_reactions_count": reactions,
        "comments_count": comments,
        "reading_time_minutes": 4,
        "tag_list": list(tags),
        "user": {"username": f"user{article_id}", "name": "Someone"},
        "ai_disclosure_level": "not_disclosed",
    }


class FakeApi:
    def __init__(self, tags=None, search=None, comments=None, fail=()):
        self.tags = tags or {}
        self.search = search or []
        self.comments = comments or {}
        self.fail = set(fail)
        self.calls = []

    def __call__(self, method, url, headers=None, **kwargs):
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.path == "/api/articles":
            if "tag" in self.fail:
                raise OSError("tag feed down")
            return self.tags.get(query["tag"], [])
        if parsed.path == "/api/articles/search":
            if "search" in self.fail:
                raise OSError("search down")
            assert headers and headers.get("Accept") == "application/vnd.forem.api-v1+json"
            return self.search
        if parsed.path == "/api/comments":
            return self.comments.get(query["a_id"], [])
        raise AssertionError(f"unexpected URL {url}")


def _install(monkeypatch, api):
    monkeypatch.setattr(devto.http, "request", api)
    return api


def test_devto_is_off_by_default():
    assert "devto" not in pipeline.available_sources({}, None, x_pending=False)


def test_devto_tags_include_sources_and_search_each_activate_it():
    assert "devto" in pipeline.available_sources({"_devto_tags": ["rust"]}, None, x_pending=False)
    assert "devto" in pipeline.available_sources({"INCLUDE_SOURCES": "devto"}, None, x_pending=False)
    assert "devto" in pipeline.available_sources({}, ["devto"], x_pending=False)


def test_search_alias_maps_dev_to():
    assert pipeline.normalize_requested_sources(["dev.to"]) == ["devto"]


def test_resolve_tags_normalizes_dedupes_and_caps_by_depth():
    assert devto.resolve_tags(None) == []
    assert devto.resolve_tags(["#Rust", "async", "rust", "web dev"], depth="deep") == ["rust", "async", "webdev"]
    assert devto.resolve_tags(["a", "b", "c"], depth="quick") == ["a"]


def test_without_tags_only_search_runs(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    devto.search_devto("Claude Code", "2026-09-11", "2026-10-11")
    assert not any("tag=" in url for url in api.calls)
    assert sum("/search" in url for url in api.calls) == 1


def test_tag_feed_and_search_merge_dedupe_and_filter(monkeypatch):
    shared = _article(1, "Kubernetes autoscaling in practice", tags=("kubernetes",))
    api = _install(monkeypatch, FakeApi(
        tags={"kubernetes": [shared, _article(2, "Kubernetes operators", tags=("kubernetes",))]},
        search=[
            dict(shared),
            _article(3, "Debugging Kubernetes pods", tags=("kubernetes", "devops")),
            _article(4, "My favourite sourdough recipe", tags=("cooking",)),
        ],
    ))
    result = devto.search_devto("kubernetes", "2026-09-11", "2026-10-11", tags=["kubernetes"])
    ids = sorted(article["id"] for article in result["articles"])
    assert ids == [1, 2, 3]
    assert "error" not in result
    tag_call = next(url for url in api.calls if "tag=" in url)
    assert "top=30" in tag_call


def test_articles_outside_the_window_are_dropped(monkeypatch):
    _install(monkeypatch, FakeApi(tags={"rust": [
        _article(1, "Rust async", tags=("rust",), published="2026-10-01T00:00:00Z"),
        _article(2, "Rust async old", tags=("rust",), published="2026-08-01T00:00:00Z"),
    ]}))
    result = devto.search_devto("rust", "2026-09-11", "2026-10-11", tags=["rust"], search_query="")
    assert [article["id"] for article in result["articles"]] == [1]


def test_unknown_tag_and_offtopic_search_stay_quiet(monkeypatch):
    _install(monkeypatch, FakeApi(search=[_article(9, "Weekly frontend news", tags=("webdev",))]))
    result = devto.search_devto("Taylor Swift", "2026-09-11", "2026-10-11")
    assert result["articles"] == []
    assert "error" not in result


def test_planner_quotes_are_stripped_from_the_search_query(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    devto.search_devto("Claude Code", "2026-09-11", "2026-10-11", search_query='"Claude Code"')
    search_call = next(url for url in api.calls if "/search" in url)
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlparse(search_call).query))["q"] == "Claude Code"


def test_partial_failure_keeps_results_and_total_failure_reports_error(monkeypatch):
    _install(monkeypatch, FakeApi(search=[_article(1, "Rust async runtimes", tags=("rust",))], fail={"tag"}))
    partial = devto.search_devto("rust async", "2026-09-11", "2026-10-11", tags=["rust"])
    assert [a["id"] for a in partial["articles"]] == [1]
    assert "error" not in partial

    devto.clear_cache()
    _install(monkeypatch, FakeApi(fail={"tag", "search"}))
    failed = devto.search_devto("rust async", "2026-09-11", "2026-10-11", tags=["rust"])
    assert failed["articles"] == []
    assert "tag feed down" in failed["error"] and "search down" in failed["error"]


def test_identical_requests_are_fetched_once(monkeypatch):
    api = _install(monkeypatch, FakeApi(tags={"rust": [_article(1, "Rust", tags=("rust",))]}))
    devto.search_devto("rust", "2026-09-11", "2026-10-11", tags=["rust"], search_query="")
    devto.search_devto("rust", "2026-09-11", "2026-10-11", tags=["rust"], search_query="")
    assert sum("tag=rust" in url for url in api.calls) == 1


def test_parse_maps_engagement_and_metadata():
    items = devto.parse_devto_response(
        {"articles": [_article(7, "Rust async runtimes", tags=("rust",), reactions=40, comments=9)]},
        query="rust async",
    )
    item = items[0]
    assert item["id"] == "7"
    assert item["engagement"] == {"reactions": 40, "comments": 9}
    assert item["date"] == "2026-10-05"
    assert item["author"] == "user7"
    assert item["metadata"]["ai_disclosure_level"] == "not_disclosed"
    assert 0 < item["relevance"] <= 1


def _source_items(articles):
    parsed = devto.parse_devto_response({"articles": articles}, query="rust")
    return normalize.normalize_source_items("devto", parsed, "2026-09-11", "2026-10-11")


def test_enrichment_targets_ranked_survivors_and_drops_spam(monkeypatch):
    api = _install(monkeypatch, FakeApi(comments={
        "1": [
            {"body_html": "<p>Great &amp; useful</p>", "user": {"username": "alice"}, "children": [{}, {}]},
            {"body_html": "<p>Official update tr.ee/dev-to</p>", "user": {"username": "bot"}, "children": []},
        ],
        "3": [{"body_html": "<p>Nice</p>", "user": {"username": "bob"}, "children": []}],
    }))
    items = _source_items([
        _article(1, "Rust one", comments=4),
        _article(2, "Rust two", comments=0),
        _article(3, "Rust three", comments=1),
        _article(4, "Rust four", comments=5),
    ])
    devto.enrich_source_items(items, top_k=2)

    first = items[0].metadata["top_comments"]
    assert [c["excerpt"] for c in first] == ["Great & useful"]
    assert first[0]["score"] == 2 and first[0]["author"] == "alice"
    assert not items[1].metadata.get("top_comments")
    assert items[2].metadata["top_comments"][0]["excerpt"] == "Nice"
    assert not items[3].metadata.get("top_comments")
    assert not any("a_id=2" in url or "a_id=4" in url for url in api.calls)


def test_normalized_item_carries_comments_into_snippet_and_container():
    parsed = devto.parse_devto_response({"articles": [_article(5, "Rust five")]}, query="rust")
    parsed[0]["top_comments"] = [{"author": "c", "text": "Solid write-up", "replies": 1}]
    item = normalize.normalize_source_items("devto", parsed, "2026-09-11", "2026-10-11")[0]
    assert item.container == "dev.to"
    assert item.metadata["top_comments"][0]["excerpt"] == "Solid write-up"
    assert "Solid write-up" in item.body


def _diagnose_sources(argv, config=None):
    import json

    from tests.test_diagnose_compat import FAKE_KEYLESS_CONFIG, _run_cli

    rc, out = _run_cli(["--diagnose", *argv], {**FAKE_KEYLESS_CONFIG, **(config or {})})
    assert rc == 0
    return json.loads(out)["available_sources"]


def test_cli_devto_tags_activate_the_source_through_diagnose():
    assert "devto" not in _diagnose_sources([])
    assert "devto" in _diagnose_sources(["--devto-tags", "rust"])


def test_cli_devto_tags_survive_an_explicit_search_list():
    assert "devto" in _diagnose_sources(["--devto-tags", "rust", "--search", "reddit"])


def test_exclude_sources_beats_devto_tags():
    assert "devto" not in _diagnose_sources(["--devto-tags", "rust"], {"EXCLUDE_SOURCES": "devto"})


def test_shortener_hidden_in_href_is_dropped(monkeypatch):
    _install(monkeypatch, FakeApi(comments={"1": [
        {"body_html": '<p><a href="https://tr.ee/dev-to">Official platform update</a></p>', "user": {"username": "bot"}, "children": []},
        {"body_html": "<p>Real feedback</p>", "user": {"username": "dev"}, "children": []},
    ]}))
    assert [c["text"] for c in devto._fetch_comments("1")] == ["Real feedback"]


def test_concurrent_misses_fetch_a_url_once(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    calls = []
    lock = threading.Lock()

    def slow_request(method, url, headers=None, **kwargs):
        with lock:
            calls.append(url)
        time.sleep(0.05)
        return [_article(1, "Rust", tags=("rust",))]

    monkeypatch.setattr(devto.http, "request", slow_request)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: devto._get_list("https://dev.to/api/articles?tag=rust"), range(4)))
    assert len(calls) == 1
    assert all(r[0]["id"] == 1 for r in results)


def test_doctor_reports_devto_as_opt_in_until_enabled():
    from lib import doctor

    assert doctor._devto_record({})["status"] == "opt-in"
    assert doctor._devto_record({"INCLUDE_SOURCES": "devto"})["status"] == "ok"


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


def test_activated_devto_joins_a_host_plan_that_omits_it():
    plan = _plan(["hackernews"])
    pipeline._ensure_devto_in_plan(plan, ["hackernews", "devto"])
    assert "devto" in plan.subqueries[0].sources
    assert "devto" in plan.source_weights


def test_inactive_devto_is_not_added_to_a_plan():
    plan = _plan(["hackernews"])
    pipeline._ensure_devto_in_plan(plan, ["hackernews"])
    assert plan.subqueries[0].sources == ["hackernews"]


def test_doctor_never_live_probes_devto():
    from lib import doctor

    assert "devto" not in doctor._probeable_sources()


def test_unexpected_response_shape_is_an_error_not_an_empty_result(monkeypatch):
    calls = []

    def object_response(method, url, headers=None, **kwargs):
        calls.append(url)
        return {"error": "changed"}

    monkeypatch.setattr(devto.http, "request", object_response)
    result = devto.search_devto("rust", "2026-09-11", "2026-10-11", tags=["rust"])
    assert result["articles"] == []
    assert "unexpected dev.to response shape" in result["error"]
    devto.search_devto("rust", "2026-09-11", "2026-10-11", tags=["rust"])
    assert len(calls) == 4


def test_requests_carry_a_total_deadline(monkeypatch):
    seen = {}

    def capture(method, url, headers=None, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(devto.http, "request", capture)
    devto._get_list("https://dev.to/api/articles?tag=rust")
    assert seen["owned_get"] is True
    assert seen["deadline_monotonic"] is not None


def test_enrichment_stops_waiting_at_its_time_limit(monkeypatch):
    import threading
    import time

    release = threading.Event()

    def stuck(article_id):
        release.wait(5)
        return [{"author": "a", "text": "late", "replies": 0}]

    monkeypatch.setattr(devto, "_fetch_comments", stuck)
    monkeypatch.setattr(devto, "ENRICH_WAIT_SECONDS", 0.2)
    items = _source_items([_article(1, "Rust one", comments=3)])
    started = time.monotonic()
    devto.enrich_source_items(items)
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 2
    assert not items[0].metadata.get("top_comments")


def test_existing_devto_route_is_left_alone():
    from lib import schema

    plan = _plan(["hackernews"])
    plan.subqueries.append(schema.SubQuery(label="focused", search_query="claude code hooks",
                                           ranking_query="q", sources=["devto"], weight=1.0))
    pipeline._ensure_devto_in_plan(plan, ["hackernews", "devto"])
    assert plan.subqueries[0].sources == ["hackernews"]
    assert plan.subqueries[1].sources == ["devto"]
