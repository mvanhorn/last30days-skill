"""On "X control my computer", results of the capability-only searches reach the report.

Posts naming the host match several searches and outrank a general tool such as
an MCP server on fused rank, and the reranker capped anything that never names
the host. Playwright MCP and similar tools were retrieved, then cut.
"""

from __future__ import annotations

from lib import fusion, planner, rerank, schema

TOPIC = "best Grok AI bot to control my computer agent"
SOURCES = ["reddit", "grounding", "github"]


def _candidate(cid: str, title: str, labels: list[str], rrf: float) -> schema.Candidate:
    item = schema.SourceItem(
        item_id=cid,
        source="grounding",
        title=title,
        body=title,
        url=f"https://e.com/{cid}",
    )
    return schema.Candidate(
        candidate_id=cid,
        item_id=cid,
        source="grounding",
        title=title,
        url=f"https://e.com/{cid}",
        snippet=title,
        subquery_labels=labels,
        native_ranks={"grounding": 1},
        local_relevance=0.6,
        freshness=50,
        engagement=None,
        source_quality=0.6,
        rrf_score=rrf,
        sources=["grounding"],
        source_items=[item],
    )


def _plan(topic: str = TOPIC) -> schema.QueryPlan:
    return planner._fallback_plan(topic, SOURCES, None, "default")


def test_plan_names_the_host_and_its_capability_searches():
    plan = _plan()
    assert "enablement-host:grok" in plan.notes
    assert fusion.capability_labels(plan) == {"capability-1", "capability-2"}


def test_topic_without_a_named_host_has_no_capability_searches():
    plan = _plan("agent that automates the desktop")
    assert "enablement" in plan.notes
    assert fusion.capability_labels(plan) == set()


def test_ordinary_topic_has_no_capability_searches():
    assert fusion.capability_labels(_plan("best wireless earbuds")) == set()


def test_pool_keeps_capability_results_that_rank_last():
    host_posts = [
        _candidate(f"grok-{i}", f"Grok Bot post {i}", ["primary"], 1.0 - i / 100)
        for i in range(10)
    ]
    tools = [
        _candidate(f"tool-{i}", f"MCP server {i}", ["capability-1"], 0.01)
        for i in range(6)
    ]
    pool = fusion._diversify_pool(
        host_posts + tools, 8, capability={"capability-1"}, host="grok"
    )
    kept = {c.candidate_id for c in pool}
    # One source: at most three reserved slots.
    assert {"tool-0", "tool-1", "tool-2"} <= kept
    assert "tool-5" not in kept


def _sourced(cid: str, title: str, source: str, rrf: float) -> schema.Candidate:
    candidate = _candidate(cid, title, ["capability-1"], rrf)
    candidate.source = source
    return candidate


def test_capability_slots_are_shared_across_sources():
    # Social posts outrank tool pages on fused rank; the tool pages still get slots.
    posts = [
        _sourced(f"post-{i}", f"AI controls your desktop {i}", "reddit", 0.5 - i / 100)
        for i in range(6)
    ]
    pages = [
        _sourced(f"page-{i}", f"MCP server {i}", "grounding", 0.1 - i / 100)
        for i in range(3)
    ]
    reserved = fusion._capability_reservation(posts + pages)
    assert [c.candidate_id for c in reserved] == [
        "post-0",
        "page-0",
        "post-1",
        "page-1",
        "post-2",
        "page-2",
    ]


def test_pool_reserves_no_slot_for_a_capability_search_post_that_names_the_host():
    # Reddit and other sources search the raw topic whatever the subquery says,
    # so a capability label also tags posts about the host.
    host_posts = [
        _candidate(f"grok-{i}", f"Grok Bot post {i}", ["primary"], 1.0 - i / 100)
        for i in range(10)
    ]
    tagged = [
        _candidate(f"tagged-{i}", f"Grok Bot setup {i}", ["capability-1"], 0.02)
        for i in range(4)
    ]
    tools = [
        _candidate(f"tool-{i}", f"MCP server {i}", ["capability-1"], 0.01)
        for i in range(3)
    ]
    pool = fusion._diversify_pool(
        host_posts + tagged + tools, 8, capability={"capability-1"}, host="grok"
    )
    kept = {c.candidate_id for c in pool}
    assert {"tool-0", "tool-1", "tool-2"} <= kept
    assert not any(cid.startswith("tagged-") for cid in kept)


def test_pool_reserves_no_slot_for_out_of_window_capability_results():
    host_posts = [
        _candidate(f"grok-{i}", f"Grok Bot post {i}", ["primary"], 1.0 - i / 100)
        for i in range(10)
    ]
    stale = _candidate("stale", "MCP server stale", ["capability-1"], 0.02)
    stale.source_items[0].published_at = "2024-01-01"
    stale.metadata = {"range_from": "2026-10-10", "range_to": "2026-11-09"}
    fresh = _candidate("fresh", "MCP server fresh", ["capability-1"], 0.01)
    pool = fusion._diversify_pool(
        host_posts + [stale, fresh], 8, capability={"capability-1"}, host="grok"
    )
    kept = {c.candidate_id for c in pool}
    assert "fresh" in kept
    assert "stale" not in kept


def test_pool_without_capability_searches_is_unchanged():
    host_posts = [
        _candidate(f"grok-{i}", f"Grok Bot post {i}", ["primary"], 1.0 - i / 100)
        for i in range(10)
    ]
    tools = [
        _candidate(f"tool-{i}", f"MCP server {i}", ["capability-1"], 0.01)
        for i in range(6)
    ]
    pool = fusion._diversify_pool(host_posts + tools, 8)
    assert not any(c.candidate_id.startswith("tool-") for c in pool)


def test_capability_result_escapes_the_entity_cap():
    plan = _plan()
    tool = _candidate(
        "tool", "Playwright MCP browser automation", ["capability-1"], 0.01
    )
    other = _candidate("other", "Playwright MCP browser automation", ["primary"], 0.01)
    rerank._mark_capability_results([tool, other], plan)
    entity = "Grok"
    tool_score, tool_reason = rerank._fallback_tuple(tool, primary_entity=entity)
    other_score, other_reason = rerank._fallback_tuple(other, primary_entity=entity)
    assert tool_reason == "fallback-local-score (capability search)"
    assert other_reason == "fallback-local-score (entity-miss demotion)"
    assert tool_score > other_score


def test_judge_prompt_exempts_capability_results():
    plan = _plan()
    tool = _candidate(
        "tool", "Playwright MCP browser automation", ["capability-1"], 0.01
    )
    rerank._mark_capability_results([tool], plan)
    prompt = rerank._build_prompt(TOPIC, plan, [tool], "Grok")
    assert "capability_search: true" in prompt
    assert "EXEMPT from this cap" in prompt


def test_judge_prompt_has_no_exception_without_capability_results():
    plan = _plan()
    # A capability-search result that names the host is ordinary evidence.
    host = _candidate("host", "Grok Bot update", ["capability-1"], 0.5)
    rerank._mark_capability_results([host], plan)
    prompt = rerank._build_prompt(TOPIC, plan, [host], "Grok")
    assert "capability_search" not in prompt
