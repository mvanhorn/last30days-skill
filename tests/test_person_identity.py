from dataclasses import asdict, replace
import threading
from unittest.mock import Mock, patch
import pytest
from lib import dates, entity_extract, fusion, pipeline, planner, render, rerank, schema


def plan():
    return schema.QueryPlan('factual', 'strict_recent', 'story', 'Alex Rowan',
        [schema.SubQuery('primary', 'Alex Rowan Northstar Labs', 'Alex Rowan at Northstar Labs', ['x'])],
        {'x': 1.0}, person_identity_anchors=['Northstar Labs', 'Cedar Ventures'])


def item(text, author='disclosurebot', source='x'):
    return schema.SourceItem('i', source, text, text, 'https://example.invalid/post',
                             author=author, relevance_hint=1.0)


def candidate(evidence):
    return schema.Candidate('c', 'i', evidence.source, evidence.title, evidence.url,
        evidence.body, ['primary'], {'primary:x': 1}, 1.0, 100, 100000, 1.0, 1.0,
        source_items=[evidence])


@pytest.mark.parametrize('text,author,source,expected', [
    ('Alex Rowan resigns as Alloy Works secretary', 'disclosurebot', 'x', False),
    ('Alex Rowan joins NORTHSTAR LABS', 'reporter', 'x', True),
    ('Alex Rowan joins Cedar Ventures', 'reporter', 'x', True),
    ('Northstar Labsmith sponsors Alex Rowan', 'reporter', 'x', False),
    ('Shipping today', 'alex_founder', 'x', True),
    ('Shipping today', 'alex_founder', 'reddit', False),
    ('Alex Rowan resigns', 'partner', 'x', False),
])
def test_identity_gate(text, author, source, expected):
    assert entity_extract.matches_person_identity(item(text, author, source),
        plan().person_identity_anchors, 'alex_founder') is expected


@pytest.mark.parametrize('hosted', [False, True])
def test_wrong_person_cannot_rank_even_with_high_scores_or_related_author(hosted):
    provider = Mock() if hosted else None
    wrong = candidate(item('Alex Rowan resigns as Alloy Works secretary', 'partner'))
    ranked = rerank.rerank_candidates(topic='Alex Rowan', plan=plan(), candidates=[wrong],
        provider=provider, model='fake' if hosted else None, shortlist_size=10,
        resolved_handles={'alex_founder', 'partner'}, primary_handle='alex_founder')
    assert ranked == []
    if hosted:
        provider.generate_json.assert_not_called()


def test_qualified_and_first_party_evidence_survives_fallback():
    for evidence in [item('Alex Rowan at Northstar Labs'), item('Shipping today', 'alex_founder')]:
        assert rerank.rerank_candidates(topic='Alex Rowan', plan=plan(),
            candidates=[candidate(evidence)], provider=None, model=None, shortlist_size=10,
            resolved_handles={'alex_founder'}, primary_handle='alex_founder')


def test_plan_roundtrip_and_validation():
    original = plan()
    assert schema.query_plan_from_dict(asdict(original)) == original
    raw = asdict(original)
    planner.validate_external_plan(raw)
    sanitized = planner._sanitize_plan(raw, topic='Alex Rowan', depth='default',
        available_sources=['x'], requested_sources=['x'])
    assert sanitized.person_identity_anchors == original.person_identity_anchors
    for invalid in ['Northstar Labs', None, [123], [''], ['!!!']]:
        raw['person_identity_anchors'] = invalid
        with pytest.raises(ValueError, match='person_identity_anchors'):
            planner.validate_external_plan(raw)


@pytest.mark.parametrize('malformed,expected', [
    (None, []), ('Northstar Labs', []), (['Northstar Labs', '', 123], ['Northstar Labs']),
])
def test_internal_planner_keeps_plan_when_anchors_are_malformed(malformed, expected):
    raw = asdict(plan())
    raw['person_identity_anchors'] = malformed
    sanitized = planner._sanitize_plan(raw, topic='Alex Rowan', depth='default',
        available_sources=['x'], requested_sources=['x'])
    assert sanitized.subqueries
    assert sanitized.person_identity_anchors == expected


def test_namesake_does_not_expand_x_targets():
    bundle = schema.RetrievalBundle()
    bundle.items_by_source['x'] = [
        item('Alex Rowan resigns as Alloy Works secretary @alloynews'),
        item('Alex Rowan at Northstar Labs', 'qualified'),
    ]
    with patch.object(entity_extract, 'extract_entities', wraps=entity_extract.extract_entities) as extract:
        # Stop immediately after extraction; no network calls or real accounts.
        extract.side_effect = lambda reddit, posts, **kw: (_ for _ in ()).throw(StopIteration(posts))
        with pytest.raises(StopIteration) as exc:
            pipeline._run_supplemental_searches(topic='Alex Rowan', bundle=bundle, plan=plan(),
                config={}, depth='default', date_range=('2026-10-01', '2026-10-22'),
                runtime=schema.ProviderRuntime('local', '', '', x_search_backend='bird'),
                mock=False, rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
                x_handle='alex_founder')
    assert [post['author_handle'] for post in exc.value.args[0]] == ['qualified']


def test_unscoped_plan_is_compatible():
    legacy_payload = asdict(plan())
    del legacy_payload['person_identity_anchors']
    planner.validate_external_plan(legacy_payload)
    legacy_plan = schema.query_plan_from_dict(legacy_payload)
    assert legacy_plan.person_identity_anchors == []
    evidence = item('Alex Rowan resigns as Alloy Works secretary')
    assert entity_extract.matches_person_identity(evidence, [])
    assert rerank.rerank_candidates(topic='Alex Rowan', plan=legacy_plan,
        candidates=[candidate(evidence)], provider=None, model=None, shortlist_size=10)


@pytest.mark.parametrize('thin_first', [False, True])
def test_fusion_keeps_qualified_article_without_unqualified_submission(thin_first):
    qualified = item('Alex Rowan at Northstar Labs', source='grounding')
    thin = item('Alex Rowan update', source='hackernews')
    thin.engagement_score = 100000
    qualified.engagement_score = 10
    streams = [(('primary', 'grounding'), [qualified]), (('primary', 'hackernews'), [thin])]
    if thin_first:
        streams.reverse()
    scoped_plan = plan()
    scoped_plan.subqueries[0] = replace(scoped_plan.subqueries[0], sources=['grounding', 'hackernews'])
    candidates = fusion.weighted_rrf(dict(streams), scoped_plan, pool_limit=10)
    assert len(candidates) == 1
    assert candidates[0].source_items == [qualified]
    assert candidates[0].source == 'grounding'
    assert candidates[0].engagement == 10
    assert rerank.rerank_candidates(topic='Alex Rowan', plan=scoped_plan,
        candidates=candidates, provider=None, model=None, shortlist_size=10)


@pytest.mark.parametrize('metadata', [
    {'transcript_snippet': 'Alex Rowan at Northstar Labs'},
    {'transcript_highlights': ['Alex Rowan at Northstar Labs']},
    {'top_comments': [{'excerpt': 'Alex Rowan at Northstar Labs'}]},
    {'top_comments': [{'text': 'Alex Rowan at Northstar Labs'}]},
    {'top_comments': ['Alex Rowan at Northstar Labs']},
    {'comment_insights': ['Alex Rowan at Northstar Labs']},
])
def test_readable_metadata_identity_anchors_are_retained(metadata):
    evidence = item('Alex Rowan interview', source='youtube')
    evidence.metadata = metadata
    assert entity_extract.matches_person_identity(evidence, plan().person_identity_anchors)
    assert rerank.rerank_candidates(topic='Alex Rowan', plan=plan(),
        candidates=[candidate(evidence)], provider=None, model=None, shortlist_size=10)


def test_nonreadable_metadata_cannot_supply_identity():
    evidence = item('Alex Rowan interview', source='youtube')
    evidence.metadata = {'thumbnail_url': 'Northstar Labs', 'transcript_highlights': [123]}
    assert not entity_extract.matches_person_identity(evidence, plan().person_identity_anchors)


@pytest.mark.parametrize('primary_handle,expected', [(None, False), ('alex_founder', True), ('partner', False)])
def test_fusion_only_exempts_explicit_x_subject(primary_handle, expected):
    evidence = item('Shipping today', author='alex_founder')
    candidates = fusion.weighted_rrf({('primary', 'x'): [evidence]}, plan(), pool_limit=10,
        primary_handle=primary_handle, first_party_handles={'alex_founder', 'partner'})
    assert bool(candidates) is expected


def test_anchored_run_drops_namesakes_from_footer_counts_and_top_voices():
    def reddit_thread(item_id, title, subreddit):
        return {'id': item_id, 'title': title, 'subreddit': subreddit,
                'url': f'https://reddit.com/r/{subreddit}/comments/{item_id}',
                'date': dates.get_date_range(5)[0], 'selftext': title,
                'engagement': {'score': 300, 'num_comments': 40, 'upvote_ratio': 0.9},
                'relevance': 0.9, 'why_relevant': 'fixture'}

    threads = [reddit_thread('R1', 'Pace Capital leads the Infisical seed round', 'venturecapital'),
               reddit_thread('R2', 'Pace Morby creative finance playbook', 'realestateinvesting')]
    with patch.object(pipeline, 'available_sources', return_value=['reddit']), \
            patch.object(pipeline, '_retrieve_stream', return_value=(threads, {})), \
            patch.object(pipeline, '_retry_thin_sources'):
        report = pipeline.run(topic='Pace Capital', config={}, depth='quick',
            requested_sources=['reddit'], web_backend='none', external_plan={
                'intent': 'research', 'freshness_mode': 'balanced_recent', 'cluster_mode': 'topic',
                'subqueries': [{'label': 'primary', 'search_query': 'Pace Capital',
                                'ranking_query': 'What is Pace Capital, the VC firm, doing?',
                                'sources': ['reddit']}],
                'person_identity_anchors': ['Pace Capital']})
    assert [i.container for i in report.items_by_source['reddit']] == ['venturecapital']
    assert report.source_status['reddit'].items_returned == 1
    assert 'realestateinvesting' not in render._top_voices_footer_line(report)
