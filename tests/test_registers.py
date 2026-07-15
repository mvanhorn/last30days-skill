from __future__ import annotations

import hashlib
import re
import sys

import pytest

import last30days as cli
from lib import env, html_render, registers, render, schema


SOURCES = [
    "reddit",
    "github",
    "youtube",
    "tiktok",
    "instagram",
    "hackernews",
    "polymarket",
    "grounding",
    "x",
    "arxiv",
    "jobs",
    "bluesky",
]


def fixture_report() -> schema.Report:
    candidates: list[schema.Candidate] = []
    clusters: list[schema.Cluster] = []
    items_by_source: dict[str, list[schema.SourceItem]] = {}
    for index, source in enumerate(SOURCES, start=1):
        item = schema.SourceItem(
            item_id=f"item-{index}",
            source=source,
            title=f"{source} signal with a detailed audience-ready headline {index}",
            body=f"Evidence body for {source}.",
            url=f"https://example.com/{source}/{index}",
            author=f"voice{index}",
            container="community",
            published_at="2026-07-09",
            date_confidence="high",
            engagement={"score": 1000 - index, "likes": 2000 - index},
            snippet=f"Technical and community evidence from {source}.",
            metadata={
                "top_comments": [
                    {
                        "excerpt": f"Memorable community reaction number {index}.",
                        "score": 1000 - index,
                        "author": f"commenter{index}",
                        "url": f"https://example.com/{source}/{index}#comment",
                    }
                ]
            },
        )
        candidate = schema.Candidate(
            candidate_id=f"candidate-{index}",
            item_id=item.item_id,
            source=source,
            title=item.title,
            url=item.url,
            snippet=item.snippet,
            subquery_labels=["primary"],
            native_ranks={f"primary:{source}": index},
            local_relevance=0.95,
            freshness=95,
            engagement=90,
            source_quality=1.0,
            rrf_score=0.02,
            sources=[source],
            source_items=[item],
            rerank_score=95,
            final_score=101 - index,
            fun_score=90,
            fun_explanation="high-signal phrasing",
        )
        cluster = schema.Cluster(
            cluster_id=f"cluster-{index}",
            title=f"{source} storyline {index}",
            candidate_ids=[candidate.candidate_id],
            representative_ids=[candidate.candidate_id],
            sources=[source],
            score=101 - index,
        )
        candidates.append(candidate)
        clusters.append(cluster)
        items_by_source[source] = [item]

    return schema.Report(
        topic="audience register research",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="fixture",
            rerank_model="fixture",
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="audience register research",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="audience register research",
                    ranking_query="What matters?",
                    sources=SOURCES,
                )
            ],
            source_weights={source: 1.0 for source in SOURCES},
        ),
        clusters=clusters,
        ranked_candidates=candidates,
        items_by_source=items_by_source,
        errors_by_source={},
        artifacts={"pre_research_flags_present": True},
    )


def _headings(output: str) -> list[str]:
    return re.findall(r"^## (.+)$", output, flags=re.MULTILINE)


def _cluster_count(output: str) -> int:
    evidence = output.split("## Ranked Evidence Clusters", 1)[1]
    evidence = evidence.split("\n## ", 1)[0]
    return len(re.findall(r"^### \d+\.", evidence, flags=re.MULTILINE))


def _bullet_count(output: str, heading: str) -> int:
    section = output.split(f"## {heading}", 1)[1]
    section = section.split("\n## ", 1)[0]
    return len(re.findall(r'^- "', section, flags=re.MULTILINE))


@pytest.mark.parametrize(
    ("name", "expected_order", "cluster_budget", "comment_budget"),
    [
        (
            "exec",
            ["Stats", "Ranked Evidence Clusters", "Source Coverage", "Best Takes", "Top Community Comments"],
            5,
            3,
        ),
        (
            "dev",
            ["Ranked Evidence Clusters", "Source Coverage", "Stats", "Top Community Comments", "Best Takes"],
            10,
            4,
        ),
        (
            "creator",
            ["Best Takes", "Top Community Comments", "Stats", "Ranked Evidence Clusters", "Source Coverage"],
            6,
            8,
        ),
    ],
)
def test_registers_control_section_order_and_budgets(
    name: str,
    expected_order: list[str],
    cluster_budget: int,
    comment_budget: int,
):
    output = render.render_compact(fixture_report(), register=name)

    headings = _headings(output)
    assert [heading for heading in headings if heading in expected_order] == expected_order
    assert _cluster_count(output) == cluster_budget
    assert _bullet_count(output, "Top Community Comments") == comment_budget


def test_emphasis_weights_promote_audience_specific_sources():
    report = fixture_report()

    dev = render.render_compact(report, register="dev")
    creator = render.render_compact(report, register="creator")

    assert "### 1. github storyline" in dev
    assert "### 1. tiktok storyline" in creator


def test_creator_register_leads_markdown_and_html_with_best_takes():
    report = fixture_report()

    markdown = render.render_compact(report, register="creator")
    html = html_render.render_html(report, register="creator")

    assert markdown.index("## Best Takes") < markdown.index("## Ranked Evidence Clusters")
    assert html.index("<h2>Best Takes</h2>") < html.index("<h2>Ranked Evidence Clusters</h2>")


def test_default_register_is_byte_identical_when_omitted(monkeypatch):
    monkeypatch.setattr(render, "_render_badge", lambda: ["fixed badge", ""])
    monkeypatch.setattr(render, "_skill_version", lambda: "fixture")
    report = fixture_report()

    implicit = render.render_compact(report)
    explicit = render.render_compact(report, register="default")

    assert implicit == explicit
    assert hashlib.sha256(implicit.encode()).hexdigest() == (
        "3f1eeb5ca4377f52f4eebff11f21cf5beaa02deddc25db12e1b2b9b1ae67e2d0"
    )


def test_eli5_is_renderer_equivalent_to_default():
    report = fixture_report()

    assert render.render_compact(report, register="eli5") == render.render_compact(
        report, register="default"
    )


def test_cli_and_env_register_resolution():
    args = cli.build_parser().parse_args(["topic", "--register", "exec"])
    assert args.register == "exec"
    assert cli._audience_register_for_run(args, {}, None).name == "exec"

    args = cli.build_parser().parse_args(["topic"])
    assert cli._audience_register_for_run(
        args, {"LAST30DAYS_REGISTER": "creator"}, None
    ).name == "creator"
    assert cli._audience_register_for_run(
        args, {"ELI5_MODE": "true"}, None
    ).name == "eli5"
    assert cli._audience_register_for_run(
        args, {"LAST30DAYS_REGISTER": "default", "ELI5_MODE": "true"}, None
    ).name == "default"


def test_last30days_register_round_trips_from_process_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("LAST30DAYS_REGISTER", "dev")
    monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.setattr(env, "_load_keychain", lambda *args, **kwargs: {})
    monkeypatch.setattr(env, "_load_pass", lambda *args, **kwargs: {})

    assert env.get_config()["LAST30DAYS_REGISTER"] == "dev"


def test_registers_do_not_shape_drill_output():
    args = cli.build_parser().parse_args(["--drill", "cluster 1"])

    assert cli._audience_register_for_run(
        args, {"LAST30DAYS_REGISTER": "creator"}, None
    ).name == "default"


def test_registers_do_not_shape_comparison_output():
    args = cli.build_parser().parse_args(
        ["alpha", "vs", "beta", "--register=creator"]
    )

    assert cli._audience_register_for_run(args, {}, None).name == "default"


@pytest.mark.parametrize(
    "topic",
    [
        "alpha/beta",
        "alpha compared to beta",
        "difference between alpha and beta",
    ],
)
def test_registers_use_canonical_comparison_detection(topic):
    args = cli.build_parser().parse_args([topic])

    assert cli._audience_register_for_run(
        args, {"LAST30DAYS_REGISTER": "board"}, None
    ).name == "default"


def test_registered_html_excludes_source_failure_diagnostics():
    report = fixture_report()
    report.source_status["x"] = schema.SourceOutcome(
        source="x",
        state=schema.RATE_LIMITED,
        detail="HTTP 429 after retry budget",
        fix_hint="doctor",
    )
    report.errors_by_source["x"] = "private source error diagnostic"

    html = html_render.render_html(report, register="creator")

    assert "Partial Coverage" not in html
    assert "Source Errors" not in html
    assert "private source error diagnostic" not in html


def test_unknown_register_errors_cleanly():
    with pytest.raises(ValueError, match="unknown audience register"):
        registers.get_register("board")

    args = cli.build_parser().parse_args(["topic"])
    with pytest.raises(ValueError, match="unknown audience register"):
        cli._audience_register_for_run(
            args, {"LAST30DAYS_REGISTER": "board"}, None
        )

    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["topic", "--register", "board"])
    assert exc.value.code == 2


def test_unknown_configured_register_fails_before_retrieval(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.env,
        "get_config",
        lambda **_kwargs: {"LAST30DAYS_REGISTER": "board"},
    )
    monkeypatch.setattr(
        cli.pipeline,
        "diagnose",
        lambda *_args, **_kwargs: pytest.fail("retrieval preflight should not run"),
    )
    monkeypatch.setattr(sys, "argv", ["last30days.py", "test topic"])

    assert cli.main() == 2
    assert "unknown audience register 'board'" in capsys.readouterr().err


def test_creator_best_takes_honor_source_emphasis():
    from lib import registers, render

    audience = registers.get_register("creator")
    assert audience.emphasis_weights, "creator preset must define emphasis weights"
    # TikTok emphasis must exceed baseline sources like hackernews.
    assert audience.emphasis_for("tiktok") > audience.emphasis_for("hackernews")


def test_best_takes_ranking_applies_source_weights():
    from lib import render, schema

    def candidate(cid, source, fun):
        item = schema.SourceItem(
            item_id=cid, source=source, title=f"take {cid}", body="b",
            url=f"https://{source}/{cid}", published_at="2026-07-01",
            snippet="s", engagement={"likes": 10},
        )
        return schema.Candidate(
            candidate_id=cid, item_id=cid, source=source, title=f"take {cid}",
            url=item.url, snippet="s", subquery_labels=["primary"],
            native_ranks={f"primary:{source}": 1}, local_relevance=0.9,
            freshness=90, engagement=10, source_quality=0.5, rrf_score=0.1,
            final_score=90, cluster_id="cl", source_items=[item],
            fun_score=80.0,
        )

    hn = candidate("hn1", "hackernews", 80.0)
    tt = candidate("tt1", "tiktok", 80.0)
    weights = {"tiktok": 1.5, "hackernews": 1.0}
    lines = render._render_best_takes(
        [hn, tt], limit=2, threshold=70.0,
        source_weight=lambda source: weights.get(source, 1.0),
    )
    body = "\n".join(lines)
    assert body.index("TikTok") < body.index("Hacker News") or body.index("tiktok") < body.index("hackernews") if "tiktok" in body.lower() else True
    # Structural assertion: the tiktok take renders before the HN take.
    tt_pos = body.lower().find("tiktok")
    hn_pos = body.lower().find("hacker")
    assert tt_pos != -1 and hn_pos != -1
    assert tt_pos < hn_pos
