"""Footer rendering: non-populated sources are dropped from the emoji tree.

Covers U1 of the doctor-classification-and-footer-noresults plan. A source that
returned zero items - whether it completed cleanly (NO_RESULTS) or failed
(RATE_LIMITED / UNREACHABLE) - must not appear as its own emoji-tree line. The
failure signal stays visible in the evidence blocks (## Partial Coverage /
## Source Coverage) so synthesis still sees it (R7), just not in the user-facing
footer.
"""

from lib import health, render, schema


def _report(*, items_by_source=None, source_status=None, errors_by_source=None):
    return schema.Report(
        topic="test topic",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T18:22:03Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model="test-planner",
            rerank_model="test-reranker",
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic="test topic",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="test topic",
                    ranking_query="test topic",
                    sources=["reddit"],
                )
            ],
            source_weights={"reddit": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source=items_by_source or {},
        errors_by_source=errors_by_source or {},
        source_status=source_status or {},
    )


def _reddit_item():
    return schema.SourceItem(
        item_id="r1",
        source="reddit",
        title="A thread",
        body="body",
        url="https://reddit.com/r/test/comments/1",
    )


def test_footer_omits_clean_no_results_sources():
    report = _report(
        items_by_source={"reddit": [_reddit_item()]},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=1),
            "jobs": schema.SourceOutcome(source="jobs", state=schema.NO_RESULTS),
            "polymarket": schema.SourceOutcome(source="polymarket", state=schema.NO_RESULTS),
            "youtube": schema.SourceOutcome(source="youtube", state=schema.NO_RESULTS),
        },
    )

    text = render.render_compact(report)

    # Populated source stays.
    assert "🟠 Reddit: 1 thread" in text
    # Clean zero-result sources do not get a footer line.
    assert "Jobs: no results" not in text
    assert "Polymarket: no results" not in text
    assert "YouTube: no results" not in text


def test_footer_omits_errored_zero_item_source_but_keeps_evidence():
    report = _report(
        items_by_source={"reddit": [_reddit_item()]},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=1),
            "x": schema.SourceOutcome(
                source="x",
                state=schema.RATE_LIMITED,
                detail="HTTP 429 after retry budget",
                fix_hint="doctor",
            ),
        },
        errors_by_source={"x": "HTTP 429 after retry budget"},
    )

    text = render.render_compact(report)

    # The failed zero-item source is dropped from the emoji-tree footer.
    assert "🔵 X: rate-limited" not in text
    # ... but its failure is still visible to synthesis in the evidence blocks (R7).
    assert "## Partial Coverage" in text
    assert "Do not interpret a failed source as no discussion" in text


def test_footer_preserves_save_path_when_all_sources_empty():
    # Every source returned zero items -> no source lines, but the durable
    # raw-file citation must still render (regression guard for the U1 loop
    # removal, which previously suppressed the whole footer incl. save path).
    report = _report(
        source_status={
            "jobs": schema.SourceOutcome(source="jobs", state=schema.NO_RESULTS),
            "x": schema.SourceOutcome(
                source="x", state=schema.RATE_LIMITED, detail="429", fix_hint="doctor"
            ),
        },
    )
    footer = render._render_emoji_footer(report, "/tmp/l30d-scratch/topic-raw.md")

    text = "\n".join(footer)
    assert "✅ All agents reported back!" in text
    assert "Raw results saved to /tmp/l30d-scratch/topic-raw.md" in text
    # No per-source line for the zero-item sources.
    assert "Jobs" not in text
    assert "rate-limited" not in text


def test_footer_empty_with_no_save_path_returns_nothing():
    report = _report(
        source_status={"jobs": schema.SourceOutcome(source="jobs", state=schema.NO_RESULTS)},
    )
    assert render._render_emoji_footer(report, None) == []


def test_library_block_carries_explainer_when_populated():
    report = _report(items_by_source={"reddit": [_reddit_item()]})
    report.library_context = [
        schema.LibraryContext(
            topic="test topic",
            published_date="2026-07-01",
            headline="a prior finding",
            summary="a prior finding",
            source_kind="brief",
        )
    ]

    text = render.render_compact(report)

    assert "## From your library" in text
    assert "Prior saved runs" in text
    assert "LAST30DAYS_LIBRARY_CONTEXT=off" in text


def test_library_block_and_explainer_absent_when_empty():
    report = _report(items_by_source={"reddit": [_reddit_item()]})
    text = render.render_compact(report)
    assert "## From your library" not in text
    assert "Prior saved runs" not in text


def test_footer_keeps_partial_populated_source_with_warning():
    """A source that returned SOME items but then failed stays in the footer
    with its ⚠ suffix - only zero-item sources are dropped."""
    ig_item = schema.SourceItem(
        item_id="ig1",
        source="instagram",
        title="A reel",
        body="caption",
        url="https://instagram.com/reel/1",
    )
    report = _report(
        items_by_source={"reddit": [_reddit_item()], "instagram": [ig_item]},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=1),
            "instagram": schema.SourceOutcome(
                source="instagram",
                state=schema.PARTIAL,
                items_returned=1,
                detail="HTTP 400: Bad Request",
                fix_hint="doctor",
            ),
        },
    )

    text = render.render_compact(report)

    assert "📸 Instagram: 1 reel" in text
    assert "⚠" in text  # partial suffix preserved on the populated line
