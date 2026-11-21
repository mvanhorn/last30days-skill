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


# -- person runs ---------------------------------------------------------------

PERSON = 'Dana Okafor'
PERSON_PLAN = {
    'intent': 'research', 'freshness_mode': 'balanced_recent', 'cluster_mode': 'topic',
    'subqueries': [{'label': 'primary', 'search_query': PERSON,
                    'ranking_query': f'What has {PERSON} done lately?',
                    'sources': ['grounding']}],
}
NAMESAKE_URL = 'https://food.example/lisbon/dana-okafor-bistro'
NEWS_URL = 'https://news.example/freight/dana-okafor-coo'


def _person_ctx(**overrides):
    from lib import person
    ctx = person.PersonContext(full_name=PERSON, employer_name='Northwind Freight',
        employer_domain='northwind.example',
        anchors=['Northwind Freight', 'northwind.example'], linkedin_status='no key')
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _web(url, title, snippet):
    return {'title': title, 'snippet': snippet, 'url': url,
            'date': dates.get_date_range(5)[0], 'relevance': 0.9}


NAMESAKE = _web(NAMESAKE_URL, 'Dana Okafor opens a new bistro in Lisbon',
                'Chef Dana Okafor brings her tasting menu to the old town.')
NEWS = _web(NEWS_URL, 'Dana Okafor named COO at Northwind Freight',
            'Northwind Freight promoted Dana Okafor to chief operating officer.')


def _person_run(config, web_items, *, github_user=None, github_items=()):
    def retrieve(*_args, source=None, **_kwargs):
        return (list(web_items), {}) if source == 'grounding' else ([], {})

    with patch.object(pipeline, '_retrieve_stream', side_effect=retrieve), \
            patch.object(pipeline, '_retry_thin_sources'), \
            patch.object(pipeline.github, 'search_github_person', return_value=list(github_items)):
        return pipeline.run(topic=PERSON, config=config, depth='quick',
            requested_sources=['grounding', 'github'], web_backend='brave',
            external_plan=PERSON_PLAN, github_user=github_user)


def _urls(report, source):
    return [i.url for i in report.items_by_source.get(source, [])]


def test_person_run_drops_and_counts_a_namesake_and_keeps_the_anchored_article():
    ctx = _person_ctx()
    report = _person_run({'_person': ctx}, [NAMESAKE, NEWS])
    assert _urls(report, 'grounding') == [NEWS_URL]
    assert NAMESAKE_URL not in {c.url for c in report.ranked_candidates}
    assert NEWS_URL in {c.url for c in report.ranked_candidates}
    assert report.artifacts['person_profile']['dropped_counts'] == {'grounding': 1}
    assert report.query_plan.person_run is True
    assert report.query_plan.person_identity_anchors == ['Northwind Freight', 'northwind.example']


def test_person_anchors_join_host_plan_anchors_once_ignoring_case():
    ctx = _person_ctx()
    plan_with_anchors = {**PERSON_PLAN, 'person_identity_anchors': ['NORTHWIND FREIGHT', 'Cedar Ventures']}
    with patch.object(pipeline, '_retrieve_stream', return_value=([], {})), \
            patch.object(pipeline, '_retry_thin_sources'):
        report = pipeline.run(topic=PERSON, config={'_person': ctx}, depth='quick',
            requested_sources=['grounding'], web_backend='brave', external_plan=plan_with_anchors)
    assert report.query_plan.person_identity_anchors == [
        'NORTHWIND FREIGHT', 'Cedar Ventures', 'northwind.example']


def test_freemail_only_person_run_drops_the_namesake_and_keeps_first_party_activity():
    ctx = _person_ctx(employer_name='', employer_domain='', anchors=[], identity_weak=True)
    commit = {'id': 'GH1', 'title': 'dokafor/route-planner: add dock scheduling', 'snippet': '',
              'url': 'https://github.com/dokafor/route-planner/pull/7', 'author': 'dokafor',
              'container': 'dokafor/route-planner', 'date': dates.get_date_range(3)[0],
              'relevance': 0.9}
    report = _person_run({'_person': ctx}, [NAMESAKE], github_user='dokafor', github_items=[commit])
    assert _urls(report, 'grounding') == []
    assert _urls(report, 'github') == [commit['url']]
    assert report.artifacts['person_profile']['dropped_counts'] == {'grounding': 1}
    assert report.artifacts['person_profile']['first_party_in_window'] == 1


def test_non_person_run_without_anchors_keeps_the_namesake():
    report = _person_run({}, [NAMESAKE])
    assert _urls(report, 'grounding') == [NAMESAKE_URL]
    assert 'person_profile' not in report.artifacts
    assert report.query_plan.person_run is False


def test_marked_first_party_post_passes_without_employer_text():
    post = item('Kicking off the winter peak plan with the dispatch team.', PERSON, 'linkedin_profile')
    post.metadata = {'identity_anchor': True, 'is_reshare': False}
    assert entity_extract.matches_person_identity(post, ['Northwind Freight'], person_run=True)
    assert entity_extract.matches_person_identity(post, [], person_run=True)


@pytest.mark.parametrize('anchors', [[], ['Northwind Freight']])
def test_person_run_gate_drops_unmarked_name_only_items(anchors):
    namesake = item('Chef Dana Okafor opens a bistro in Lisbon', 'foodnews', 'grounding')
    assert not entity_extract.matches_person_identity(namesake, anchors, person_run=True)
    assert entity_extract.matches_person_identity(namesake, [])


def test_person_run_gate_keeps_the_resolved_x_subject_with_no_anchors():
    post = item('Shipping the new dock schedule today', 'dokafor', 'x')
    assert entity_extract.matches_person_identity(post, [], 'dokafor', person_run=True)
    assert not entity_extract.matches_person_identity(post, [], 'someone_else', person_run=True)


def test_backfilled_transcript_anchor_passes_the_person_run_gate():
    video = item('Dana Okafor on last-mile logistics', 'logisticspod', 'youtube')
    video.metadata = {'transcript_snippet': 'I run operations at Northwind Freight these days.'}
    assert entity_extract.matches_person_identity(video, ['Northwind Freight'], person_run=True)
    video.metadata = {}
    assert not entity_extract.matches_person_identity(video, ['Northwind Freight'], person_run=True)


def test_rerank_and_fusion_apply_the_person_run_gate_with_no_anchors():
    scoped = replace(plan(), person_identity_anchors=[], person_run=True)
    namesake = item('Chef Dana Okafor opens a bistro in Lisbon', 'foodnews')
    assert rerank.rerank_candidates(topic=PERSON, plan=scoped, candidates=[candidate(namesake)],
        provider=None, model=None, shortlist_size=10) == []
    assert fusion.weighted_rrf({('primary', 'x'): [namesake]}, scoped, pool_limit=10) == []


def test_person_run_flag_round_trips_and_old_reports_load():
    scoped = replace(plan(), person_run=True)
    assert schema.query_plan_from_dict(asdict(scoped)).person_run is True
    legacy = asdict(plan())
    del legacy['person_run']
    assert schema.query_plan_from_dict(legacy).person_run is False
