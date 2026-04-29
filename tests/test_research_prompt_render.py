# ruff: noqa: E402
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "last30days" / "scripts"))

import last30days as cli
from lib import render, schema


def _runtime() -> schema.ProviderRuntime:
    return schema.ProviderRuntime(
        reasoning_provider="gemini",
        planner_model="gemini-3.1-flash-lite-preview",
        rerank_model="gemini-3.1-flash-lite-preview",
    )


def _plan(topic: str, sources: list[str]) -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="comparison",
        freshness_mode="balanced_recent",
        cluster_mode="debate",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=topic,
                sources=sources,
            )
        ],
        source_weights={source: 1.0 for source in sources},
    )


def _source_item(
    item_id: str,
    source: str,
    title: str,
    url: str,
    *,
    author: str | None = None,
    container: str | None = None,
    engagement: dict[str, int] | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=f"Body for {title}",
        url=url,
        author=author,
        container=container,
        published_at="2026-04-20",
        date_confidence="high",
        engagement=engagement or {},
        snippet=f"Snippet for {title}",
    )


def _candidate(
    candidate_id: str,
    item: schema.SourceItem,
    *,
    sources: list[str] | None = None,
    source_items: list[schema.SourceItem] | None = None,
    score: float = 90,
) -> schema.Candidate:
    return schema.Candidate(
        candidate_id=candidate_id,
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=["primary"],
        native_ranks={f"primary:{item.source}": 1},
        local_relevance=0.9,
        freshness=7,
        engagement=sum(item.engagement.values()) if item.engagement else None,
        source_quality=1.0,
        rrf_score=0.03,
        sources=sources or [item.source],
        source_items=source_items or [item],
        final_score=score,
    )


def rich_report() -> schema.Report:
    reddit = _source_item(
        "r1",
        "reddit",
        "LangGraph wins production workflows",
        "https://reddit.com/r/LangChain/comments/abc",
        container="LangChain",
        engagement={"score": 344, "num_comments": 119},
    )
    web = _source_item(
        "w1",
        "grounding",
        "LangGraph documentation",
        "https://docs.langchain.com/langgraph",
        container="docs.langchain.com",
    )
    x_post = _source_item(
        "x1",
        "x",
        "CrewAI stays fastest for prototypes",
        "https://x.com/joaomdmoura/status/1",
        author="joaomdmoura",
        engagement={"likes": 1200, "reposts": 90},
    )
    hn = _source_item(
        "hn1",
        "hackernews",
        "HN wants explicit state machines",
        "https://news.ycombinator.com/item?id=1",
        container="news.ycombinator.com",
        engagement={"points": 88, "comments": 42},
    )
    candidates = [
        _candidate("c1", reddit, sources=["reddit", "grounding"], source_items=[reddit, web], score=92),
        _candidate("c2", x_post, score=85),
        _candidate("c3", hn, score=75),
    ]
    clusters = [
        schema.Cluster("cl1", "LangGraph wins production workflows", ["c1"], ["c1"], ["reddit", "grounding"], 92),
        schema.Cluster("cl2", "CrewAI stays fastest for prototypes", ["c2"], ["c2"], ["x"], 85, "single-source"),
        schema.Cluster("cl3", "HN wants explicit state machines", ["c3"], ["c3"], ["hackernews"], 75),
    ]
    return schema.Report(
        topic="Agent frameworks",
        range_from="2026-03-30",
        range_to="2026-04-29",
        generated_at="2026-04-29T00:00:00+00:00",
        provider_runtime=_runtime(),
        query_plan=_plan("Agent frameworks", ["grounding", "reddit", "x", "hackernews"]),
        clusters=clusters,
        ranked_candidates=candidates,
        items_by_source={
            "grounding": [web],
            "reddit": [reddit],
            "x": [x_post],
            "hackernews": [hn],
        },
        errors_by_source={},
        artifacts={
            "resolved": {
                "entity": "Agent frameworks",
                "x_handle": "LangChainAI",
                "subreddits": ["LangChain", "LocalLLaMA"],
                "github_user": "langchain-ai",
                "github_repos": ["langchain-ai/langgraph", "crewAIInc/crewAI"],
                "context": "LangGraph and CrewAI are the main community-named frameworks.",
            }
        },
    )


def thin_report() -> schema.Report:
    return schema.Report(
        topic="Obscure Widget",
        range_from="2026-03-30",
        range_to="2026-04-29",
        generated_at="2026-04-29T00:00:00+00:00",
        provider_runtime=_runtime(),
        query_plan=_plan("Obscure Widget", ["grounding", "reddit"]),
        clusters=[],
        ranked_candidates=[],
        items_by_source={"grounding": [], "reddit": []},
        errors_by_source={},
    )


class ResearchPromptRenderTests(unittest.TestCase):
    def test_rich_report_snapshot(self):
        rendered = render.render_research_prompt(rich_report())
        expected = """**Topic:** Agent frameworks

**Community pre-research summary**
The 30-day community signal for Agent frameworks clusters around LangGraph wins production workflows, CrewAI stays fastest for prototypes, and HN wants explicit state machines.
The strongest coverage comes from Web, Reddit, X, and Hacker News, with 4 usable items feeding 3 ranked clusters.
Use these community claims as hypotheses to verify, contradict, or quantify with primary-source research.

**Resolved entities**
- **Agent frameworks**: X @LangChainAI | Subs r/LangChain, r/LocalLLaMA | GitHub @langchain-ai (langchain-ai/langgraph, crewAIInc/crewAI) | Context: LangGraph and CrewAI are the main community-named frameworks.

**Top community claims (cited, with engagement)**
1. [LangGraph wins production workflows](https://reddit.com/r/LangChain/comments/abc) - Reddit via r/LangChain; engagement: 344pts, 119cmt; cluster score 92.
2. [CrewAI stays fastest for prototypes](https://x.com/joaomdmoura/status/1) - X via @joaomdmoura; engagement: 1,200likes, 90rt; cluster score 85.
3. [HN wants explicit state machines](https://news.ycombinator.com/item?id=1) - Hacker News via news.ycombinator.com; engagement: 88pts, 42cmt; cluster score 75.

**Gaps the social engine cannot fill**
- Independent corroboration of: CrewAI stays fastest for prototypes; HN wants explicit state machines

**Investigation directives**
1. Verify or contradict the community claim that LangGraph wins production workflows using primary sources, official documentation, or reputable reporting.
2. Find independent evidence for CrewAI stays fastest for prototypes; separate first-hand data from repeated social summaries.
3. Fill this explicit gap from the social run: Independent corroboration of: CrewAI stays fastest for prototypes; HN wants explicit state machines.
4. Return a concise brief that labels each conclusion as confirmed, contradicted, or still uncertain relative to the community signal.
"""
        self.assertEqual(expected, rendered)

    def test_handoff_omits_last30days_output_envelope(self):
        rendered = render.render_research_prompt(rich_report())
        self.assertFalse(rendered.startswith("🌐 last30days"))
        self.assertNotIn("✅ All agents reported back!", rendered)
        self.assertNotIn("\nSources:", rendered)

    def test_all_six_section_headers_present(self):
        rendered = render.render_research_prompt(rich_report())
        for header in [
            "**Topic:**",
            "**Community pre-research summary**",
            "**Resolved entities**",
            "**Top community claims (cited, with engagement)**",
            "**Gaps the social engine cannot fill**",
            "**Investigation directives**",
        ]:
            self.assertIn(header, rendered)

    def test_thin_cluster_topic_surfaces_gap_directives(self):
        rendered = render.render_research_prompt(thin_report())
        self.assertIn("no ranked clusters survived", rendered)
        self.assertIn("- Authoritative web sources / official documentation", rendered)
        self.assertIn("- Developer-community technical analysis", rendered)
        self.assertIn("No ranked community claims were strong enough to render.", rendered)

    def test_vs_mode_research_prompt(self):
        rendered = render.render_research_prompt_comparison([
            ("LangGraph", rich_report()),
            ("CrewAI", thin_report()),
        ])
        self.assertIn("**Topic:** LangGraph vs CrewAI", rendered)
        self.assertIn("LangGraph: LangGraph wins production workflows", rendered)
        self.assertIn("CrewAI", rendered)
        self.assertNotIn("✅ All agents reported back!", rendered)

    def test_cli_routes_and_save_suffix(self):
        report = rich_report()
        self.assertEqual(
            render.render_research_prompt(report),
            cli.emit_output(report, "research-prompt"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = cli.save_output(report, "research-prompt", tmp)
            self.assertEqual("agent-frameworks-raw-research-prompt.md", path.name)
            self.assertEqual(render.render_research_prompt(report), path.read_text())


if __name__ == "__main__":
    unittest.main()
