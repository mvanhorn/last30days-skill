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


def test_footer_keeps_partial_populated_source_without_warning_text():
    """A source that returned SOME items but then failed stays in the footer
    as counts only: run diagnostics live in doctor --postmortem, the saved raw
    file, and the model-facing ## Partial Coverage note, never on the
    user-facing conclusion surface."""
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
    footer = text.split("✅ All agents reported back!", 1)[1]
    assert "⚠" not in footer
    assert "run doctor" not in footer
    assert "## Partial Coverage" in text
    assert "Instagram" in text.split("## Partial Coverage", 1)[1].split("\n\n", 1)[0] or "Instagram partial" in text


def test_footer_auth_failed_populated_source_has_no_warning_text():
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
                state=schema.AUTH_FAILED,
                items_returned=1,
                detail="HTTP 401",
                fix_hint="doctor",
            ),
        },
    )

    text = render.render_compact(report)
    footer = text.split("✅ All agents reported back!", 1)[1]

    assert "📸 Instagram: 1 reel" in footer
    assert "⚠" not in footer
    assert "auth-failed" in text.split("## Partial Coverage", 1)[1]


def test_compact_drops_source_failure_warnings_and_source_errors_block():
    report = _report(
        items_by_source={"reddit": [_reddit_item()]},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=1),
            "jobs": schema.SourceOutcome(source="jobs", state=schema.UNREACHABLE, items_returned=0, detail="DNS"),
        },
    )
    report.warnings = [
        "Some sources failed: jobs",
        "Some sources returned partial results (degraded): reddit",
        "Evidence is thin for this topic.",
    ]
    report.errors_by_source = {"jobs": "URL Error: nodename nor servname provided"}

    compact = render.render_compact(report)
    assert "## Source Errors" not in compact
    assert "Some sources failed" not in compact
    assert "returned partial results" not in compact
    assert "Evidence is thin for this topic." in compact

    full = render.render_full(report)
    assert "## Source Errors" in full

    payload = schema.to_dict(report)
    assert payload["warnings"] == report.warnings
    assert payload["errors_by_source"] == {"jobs": "URL Error: nodename nor servname provided"}
    assert payload["source_status"]["jobs"]["state"] == schema.UNREACHABLE


def test_footer_carries_freshness_verdict():
    """The freshness verdict reaches the footer, not just the report body."""
    stale = [
        schema.SourceItem(
            item_id=f"r{i}",
            source="reddit",
            title=f"Old thread {i}",
            body="body",
            url=f"https://reddit.com/r/test/comments/{i}",
            published_at="2026-06-12",
            date_confidence="high",
        )
        for i in range(6)
    ]
    report = _report(
        items_by_source={"reddit": stale},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=6),
        },
    )

    text = render.render_compact(report)

    assert "🕒" in text
    assert "from the last 7 days" in text


def test_footer_freshness_line_absent_when_evidence_is_recent():
    fresh = [
        schema.SourceItem(
            item_id=f"r{i}",
            source="reddit",
            title=f"Fresh thread {i}",
            body="body",
            url=f"https://reddit.com/r/test/comments/{i}",
            published_at="2026-07-08",
            date_confidence="high",
        )
        for i in range(6)
    ]
    report = _report(
        items_by_source={"reddit": fresh},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=6),
        },
    )

    assert "🕒" not in render.render_compact(report)


def test_raw_results_only_footer_includes_freshness_line():
    """AE3: raw-results-only footer still includes the freshness line.

    When every source returns zero items (so source_lines would be empty and
    body starts empty) but a save_path is provided, the freshness verdict
    must still appear alongside the raw-results line. Without the fix,
    `if freshness_line and body:` would be False because body was empty.
    """
    report = _report(
        items_by_source={},
        source_status={},
    )

    footer = render._render_emoji_footer(report, "/tmp/l30d-scratch/topic-raw.md")
    text = "\n".join(footer)

    assert "🕒" in text
    assert "no usable dated evidence" in text
    assert "Raw results saved to /tmp/l30d-scratch/topic-raw.md" in text



def test_footer_keeps_skipped_research_receipt_when_no_sources_returned():
    report = _report()
    report.artifacts = {'fixed_research': {'status': 'skipped', 'reason': 'native_search_not_enabled', 'searches': []}}
    assert render._render_emoji_footer(report, None) == [
        '---', '✅ All agents reported back!',
        '└─ Fixed follow-up: 0 attempted, 0 succeeded; skipped; native Search unavailable.', '---',
    ]


def test_adaptive_footer_reports_latest_excerpt_counts_without_facet_text():
    report = _report()
    report.artifacts = {'adaptive_research': {
        'status': 'complete', 'reason': 'bounded_round',
        'searches': [{'status': 'ok', 'query': 'PRIVATE_QUERY'}],
        'before': {'status': 'ok', 'facets': [{'state': 'gap'}]},
        'after': {'status': 'ok', 'facets': [
            {'state': 'covered', 'question': 'PRIVATE_QUESTION'},
            {'state': 'gap'}, {'state': 'unknown'},
        ]},
    }}
    expected = (
        'Adaptive follow-up: 1 attempted, 1 succeeded; finished; planned queries finished. '
        'Public excerpt check: 1 covered, 1 gap, 1 unknown.'
    )
    for text in (render.render_compact(report), render.render_full(report),
                 render.render_full(report, save_path='/tmp/research-report.md')):
        assert expected in text
        assert 'PRIVATE_' not in text
        assert 'coverage complete' not in text.lower()


def test_adaptive_final_unknown_does_not_reuse_prior_coverage():
    report = _report()
    report.artifacts = {'adaptive_research': {
        'status': 'complete', 'reason': 'bounded_round', 'searches': [{'status': 'ok'}],
        'before': {'status': 'ok', 'facets': [{'state': 'covered'}]},
        'after': {'status': 'unknown', 'reason': 'judgment_failed'},
    }}
    text = '\n'.join(render._render_research_route(report))
    assert 'finished; planned queries finished.' in text
    assert 'Public excerpt check: unknown; coverage judgment failed.' in text
    assert '1 covered' not in text


def test_adaptive_skip_and_unknown_labels_do_not_expose_raw_errors():
    report = _report()
    report.artifacts = {'adaptive_research': {
        'status': 'skipped', 'reason': 'collection_incomplete', 'searches': [],
    }}
    assert render._render_research_route(report) == [
        'Adaptive follow-up: 0 attempted, 0 succeeded; skipped; collection incomplete.',
    ]
    report.artifacts['adaptive_research'].update(
        status=['bad'], reason='PRIVATE_PROVIDER_ERROR',
        after={'status': 'unknown', 'reason': 'PRIVATE_PROVIDER_ERROR'},
    )
    assert render._render_research_route(report) == [
        'Adaptive follow-up: 0 attempted, 0 succeeded; unknown; unknown. '
        'Public excerpt check: unknown; unknown.',
    ]


def test_pipeline_coverage_judge_failure_reaches_compact_and_full_output(tmp_path, monkeypatch):
    """Successful ranking must not hide a later malformed coverage response."""
    import socket

    from lib import pipeline

    topic = 'Rust compiler changes'
    searches = []
    judge_stages = []

    def transport(url, payload, **kwargs):
        if url.endswith('/systemone'):
            questions = payload['questions']
            is_coverage = any(name.startswith('facet_') for name in questions)
            judge_stages.append('coverage' if is_coverage else 'ranking')
            answers = {}
            if not is_coverage:
                for name, question in questions.items():
                    kind = question['type']
                    if kind == 'noul':
                        answers[name] = {'type': kind, 'noul': .99}
                    elif kind == 'choice':
                        chosen = next(iter(question['criteria']))
                        answers[name] = {'type': kind, 'choice': chosen, 'confidence': 1.,
                                         'probabilities': {key: float(key == chosen) for key in question['criteria']}}
                    else:
                        choices = question['criteria']
                        top = len(choices) - 1
                        answers[name] = {'type': kind, 'score': top, 'confidence': 1.,
                                         'probabilities': {str(i): float(i == top) for i in range(len(choices))},
                                         'legend': {str(i): label for i, label in enumerate(choices)}}
            return {'model': 'jev-1.13.0', 'answers': answers, 'usage': {}}
        assert url == 'https://api.perplexity.ai/search'
        searches.append(payload['query'])
        return {'results': [{'id': 'release', 'title': 'Rust compiler changes announced',
                            'url': 'https://example.com/rust-release',
                            'snippet': 'Rust compiler changes add diagnostics and improve compile time.',
                            'date': '2026-09-24'}]}

    def no_network(*args, **kwargs):
        raise AssertionError('network forbidden')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(pipeline.env, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(pipeline, 'available_sources', lambda *args, **kwargs: ['perplexity'])
    monkeypatch.setattr(pipeline.providers, 'resolve_runtime', lambda *args, **kwargs:
                        (schema.ProviderRuntime('local', 'deterministic', 'local-score'), None))
    monkeypatch.setattr(pipeline.http, 'post', transport)
    plan = {'intent': 'product', 'freshness_mode': 'balanced_recent', 'cluster_mode': 'theme',
            'subqueries': [{'label': 'primary', 'search_query': topic,
                            'ranking_query': topic, 'sources': ['perplexity'], 'weight': 1.0}]}
    report = pipeline.run(
        topic=topic, depth='quick', mock=False, web_backend='none',
        requested_sources=['perplexity'], external_plan=plan, as_of_date='2026-09-28',
        config={'PERPLEXITY_API_KEY': 'dummy-pplx', 'TYPESAFE_API_KEY': 'dummy-jev',
                'LAST30DAYS_JEV_PROVIDER': 'typesafe', 'LAST30DAYS_PERPLEXITY_MODE': 'search',
                '_research_policy': 'adaptive',
                '_research_facets': [{'id': 'issues', 'question': 'What failures do users report?',
                                     'query': 'Rust compiler user regressions', 'required_role': 'experience'}]},
    )
    assert judge_stages == ['ranking', 'coverage']
    assert searches == [topic]
    assert len(report.items_by_source['perplexity']) == 1
    assert report.artifacts['adaptive_research']['reason'] == 'judgment_failed'
    for text in (render.render_compact(report), render.render_full(report),
                 render.render_full(report, save_path='/tmp/research-report.md')):
        assert 'Ranking: initial: Jev (Typesafe).' in text
        assert ('Adaptive follow-up: 0 attempted, 0 succeeded; unknown; coverage judgment failed. '
                'Public excerpt check: unknown.') in text
