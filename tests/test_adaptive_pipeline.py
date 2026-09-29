"""Independent adaptive controller safety and end-to-end transport controls."""
import json
import time
from unittest.mock import patch

import pytest

from lib import adaptive_research, health, pipeline, schema

TOPIC = 'Atlas coding tool user experience'
RANGE = ('2026-09-01', '2026-09-28')
FACETS = [
    {'id': 'experience', 'question': 'What did users directly experience?', 'query': 'Atlas coding tool hands-on experience', 'required_role': 'experience', 'search_type': 'fast'},
    {'id': 'failures', 'question': 'What failed in direct use?', 'query': 'Atlas coding tool observed failures', 'required_role': 'experience', 'search_type': 'web'},
]


def answers_for(questions, *, gap=True):
    answers = {}
    for key, question in questions.items():
        kind = question['type']
        if kind == 'noul':
            answers[key] = {'type': kind, 'noul': 0.05 if key.startswith('facet_') and gap else 0.99}
        elif kind == 'choice':
            chosen = 'announcement' if gap else 'experience'
            if chosen not in question['criteria']:
                chosen = next(iter(question['criteria']))
            answers[key] = {'type': kind, 'choice': chosen, 'confidence': 1.0,
                            'probabilities': {k: float(k == chosen) for k in question['criteria']}}
        else:
            criteria = question['criteria']
            score = len(criteria) - 1
            answers[key] = {'type': kind, 'score': score, 'confidence': 1.0,
                            'probabilities': {str(i): float(i == score) for i in range(len(criteria))},
                            'legend': {str(i): label for i, label in enumerate(criteria)}}
    return answers


class FixtureJev:
    provider = 'typesafe'
    last_receipt = {'provider': 'typesafe', 'status': 'fixture', 'model': 'fixture'}
    def evaluate(self, state, questions):
        return answers_for(questions)


def item(url='https://example.com/atlas-release'):
    return schema.SourceItem('release', 'perplexity', 'Atlas coding tool release',
        'Atlas coding tool released retry support. No user trial is reported.', url,
        published_at='2026-09-20', snippet='Atlas coding tool released retry support. No user trial is reported.')


def candidate():
    source = item()
    return schema.Candidate('release', 'release', 'perplexity', source.title, source.url, source.snippet,
        ['primary'], {'perplexity': 1}, .9, 100, 0, .5, .02, source_items=[source],
        metadata={'range_from': RANGE[0], 'range_to': RANGE[1]})


def packet():
    bundle = schema.RetrievalBundle(artifacts={'grounding': []})
    bundle.add_items('primary', 'perplexity', [item()])
    plan = schema.QueryPlan('product', 'balanced_recent', 'theme', TOPIC,
        [schema.SubQuery('primary', TOPIC, TOPIC, ['perplexity'])], {'perplexity': 1.0})
    return dict(topic=TOPIC, config={'PERPLEXITY_API_KEY': 'dummy-pplx', '_perplexity_paid_budget': pipeline.PaidSourceBudget(used=1)},
        facets=FACETS, client=FixtureJev(), judge_receipt={'judge_route': 'jev:typesafe'},
        candidates=[candidate()], bundle=bundle, plan=plan, available=['perplexity'],
        runtime=schema.ProviderRuntime('local', 'deterministic', 'local-score'), depth='quick',
        date_range=RANGE, run_started=time.monotonic(), per_stream_limit=6)


@pytest.mark.parametrize('condition,reason', [
    ('no_key', 'judge_unavailable'), ('mock', 'mock'), ('source_off', 'native_search_not_enabled'),
    ('no_native_key', 'native_search_not_enabled'), ('source_budget_zero', 'source_budget_disabled'),
    ('incomplete', 'collection_incomplete'), ('failed_judge', 'judge_failed'), ('timeout', 'deadline_exceeded'),
])
def test_blocked_followups_have_zero_judge_and_search_calls(condition, reason):
    kwargs = packet()
    if condition == 'no_key': kwargs['client'] = None
    if condition == 'mock': kwargs['mock'] = True
    if condition == 'source_off': kwargs['available'] = ['grounding']
    if condition == 'no_native_key': kwargs['config'].pop('PERPLEXITY_API_KEY')
    if condition == 'source_budget_zero': kwargs['config']['_max_source_fetches'] = 0
    if condition == 'incomplete': kwargs['bundle'].source_status['reddit'] = schema.SourceOutcome('reddit', health.TIMEOUT)
    if condition == 'failed_judge': kwargs['judge_receipt'] = {'judge_route': 'deterministic'}
    if condition == 'timeout': kwargs['run_started'] -= 301
    with patch.object(adaptive_research, 'assess') as judge, patch.object(pipeline, '_retrieve_stream') as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    judge.assert_not_called(); search.assert_not_called()
    assert kwargs['bundle'].artifacts['adaptive_research']['reason'] == reason


def test_coverage_judge_failure_authorizes_no_search():
    kwargs = packet()
    with patch.object(kwargs['client'], 'evaluate', side_effect=TimeoutError('fixture')), patch.object(pipeline, '_retrieve_stream') as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    search.assert_not_called()
    assert kwargs['bundle'].artifacts['adaptive_research']['before']['status'] == 'unknown'


def test_two_queries_use_shared_budget_and_keep_dates_modes_and_query_identity():
    kwargs = packet()
    raw = [dict(id='a', title='Atlas coding tool hands-on report', url='https://example.com/atlas-user-a',
                snippet='I personally used Atlas coding tool. Its retries fail on Windows.', date='2026-09-23')]
    other = [dict(id='b', title='Atlas coding tool observed failures', url='https://example.com/atlas-user-b',
                  snippet='Atlas coding tool on Linux corrupted my cache; clearing it repaired the build.', date='2026-09-24')]
    with patch.object(pipeline, '_retrieve_stream', side_effect=[(raw, {}), (other, {})]) as search:
        assert pipeline._adaptive_followups(**kwargs) is True
    assert search.call_count == 2
    assert kwargs['config']['_perplexity_paid_budget'].used == 3
    assert [call.kwargs['subquery'].search_query for call in search.call_args_list] == [f['query'] for f in FACETS]
    assert [call.kwargs['config']['LAST30DAYS_PERPLEXITY_SEARCH_TYPE'] for call in search.call_args_list] == ['fast', 'web']
    assert all(call.kwargs['date_range'] == RANGE for call in search.call_args_list)
    assert all(call.kwargs['config']['LAST30DAYS_PERPLEXITY_MODE'] == 'search' for call in search.call_args_list)


def test_exhausted_shared_search_budget_makes_no_followup_request():
    kwargs = packet(); kwargs['config']['_perplexity_paid_budget'].used = 3
    with patch.object(pipeline, '_retrieve_stream') as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    search.assert_not_called()
    assert kwargs['bundle'].artifacts['adaptive_research']['reason'] == 'search_budget_exhausted'


def test_repeated_query_is_never_sent_again():
    kwargs = packet()
    kwargs['facets'] = [dict(FACETS[0], query='  ' + TOPIC.upper() + '  ')]
    with patch.object(pipeline, '_retrieve_stream') as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    search.assert_not_called()


def test_no_new_evidence_stops_before_second_followup():
    kwargs = packet()
    with patch.object(pipeline, '_retrieve_stream', return_value=([], {})) as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    assert search.call_count == 1


def test_duplicate_url_is_not_added_or_used_to_continue_round():
    kwargs = packet()
    duplicate = dict(id='another-id', title=item().title, url=item().url, snippet=item().snippet, date='2026-09-20')
    with patch.object(pipeline, '_retrieve_stream', return_value=([duplicate], {})) as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    assert search.call_count == 1
    assert len(kwargs['bundle'].items_by_source['perplexity']) == 1


def test_mock_pipeline_preserves_default_results_and_makes_no_jev_call():
    base = dict(topic=TOPIC, depth='quick', requested_sources=['perplexity'], mock=True,
                web_backend='none', as_of_date=RANGE[1])
    with patch.object(pipeline.http, 'post') as http_post, patch.object(pipeline.jev, 'resolve') as resolve:
        normal = pipeline.run(config={}, **base)
        adaptive = pipeline.run(config={'_research_facets': FACETS, 'LAST30DAYS_JEV_PROVIDER': 'auto', 'TYPESAFE_API_KEY': 'dummy-key'}, **base)
    http_post.assert_not_called(); resolve.assert_not_called()
    assert [c.candidate_id for c in normal.ranked_candidates] == [c.candidate_id for c in adaptive.ranked_candidates]
    assert adaptive.artifacts['adaptive_research']['reason'] == 'mock'


def test_real_pipeline_with_fixture_http_adds_public_evidence_keeps_private_out(tmp_path, monkeypatch):
    private_dir = tmp_path / 'notes'; private_dir.mkdir()
    (private_dir / 'private.md').write_text('Atlas coding tool user experience: PRIVATE_EVAL_CANARY local observation.')
    monkeypatch.setattr(pipeline.env, 'CONFIG_DIR', tmp_path / 'config')
    calls = []
    def post(url, payload, **kwargs):
        calls.append((url, payload))
        if 'systemone' in url:
            state = payload['state']
            gap = 'hands-on Windows failure' not in state
            return {'model': 'jev-1.13', 'answers': answers_for(payload['questions'], gap=gap),
                    'usage': {'input_tokens': 100, 'output_tokens': 10}}
        assert url == 'https://api.perplexity.ai/search'
        followup = payload['query'] != TOPIC
        return {'results': [{'id': 'followup' if followup else 'release',
            'title': 'Atlas coding tool hands-on Windows failure' if followup else item().title,
            'url': 'https://example.com/atlas-user' if followup else item().url,
            'snippet': 'I personally used Atlas coding tool: hands-on Windows failure after retry.' if followup else item().snippet,
            'date': '2026-09-24'}]}
    external_plan = {'intent': 'product', 'freshness_mode': 'balanced_recent', 'cluster_mode': 'theme',
        'subqueries': [{'label': 'primary', 'search_query': TOPIC, 'ranking_query': TOPIC,
                        'sources': ['perplexity'], 'weight': 1.0}]}
    with patch.object(pipeline, 'available_sources', return_value=['perplexity']), patch.object(pipeline.http, 'post', side_effect=post):
        report = pipeline.run(topic=TOPIC, depth='quick', mock=False, web_backend='none',
            requested_sources=['perplexity'], external_plan=external_plan, as_of_date=RANGE[1],
            corpus_dirs=[str(private_dir)], corpus_all_time=True,
            config={'PERPLEXITY_API_KEY': 'dummy-pplx', 'TYPESAFE_API_KEY': 'dummy-jev',
                    'LAST30DAYS_PERPLEXITY_MODE': 'search',
                    'LAST30DAYS_PERPLEXITY_SEARCH_TYPE': 'fast', '_research_facets': FACETS[:1]})
    assert 'PRIVATE_EVAL_CANARY' not in json.dumps(calls)
    assert any(c.source == 'corpus' for c in report.ranked_candidates)
    assert any(c.url == 'https://example.com/atlas-user' for c in report.ranked_candidates)
    searches = [(url, body) for url, body in calls if url.endswith('/search')]
    assert len(searches) == 2
    assert searches[1][1]['query'] == FACETS[0]['query']
    assert all(body['search_before_date_filter'] == '09/28/2026' for _, body in searches)
    assert report.artifacts['adaptive_research']['after']['facets'][0]['state'] == 'covered'


@pytest.mark.parametrize("policy", ["fixed", "adaptive"])
@pytest.mark.parametrize("selection", [
    {}, {"LAST30DAYS_JEV_PROVIDER": "auto"},
    {"LAST30DAYS_JEV_PROVIDER": "auto", "TYPESAFE_API_KEY": " ", "OPENROUTER_API_KEY": "\t"},
    {"LAST30DAYS_JEV_PROVIDER": "off", "TYPESAFE_API_KEY": "dummy-key"},
    {"LAST30DAYS_JEV_PROVIDER": "typesafe", "OPENROUTER_API_KEY": "dummy-other-route"},
    {"LAST30DAYS_JEV_PROVIDER": "openrouter", "TYPESAFE_API_KEY": "dummy-other-route"},
])
@pytest.mark.parametrize("mock", [False, True])
def test_missing_or_off_route_never_enters_optional_pipeline(policy, selection, mock):
    plan = {"intent": "product", "freshness_mode": "balanced_recent", "cluster_mode": "theme",
            "subqueries": [{"label": "primary", "search_query": TOPIC, "ranking_query": TOPIC,
                            "sources": ["perplexity"], "weight": 1.0}]}
    base = dict(topic=TOPIC, depth="quick", requested_sources=["perplexity"],
                mock=mock, web_backend="none", as_of_date=RANGE[1], external_plan=plan)
    runtime = schema.ProviderRuntime("local", "deterministic", "local-score")
    raw = [{"id": "release", "title": item().title, "url": item().url,
            "snippet": item().snippet, "date": "2026-09-20"}]
    config = {**selection, "_research_facets": {"invalid": "must not validate"}, "_research_policy": policy}
    with patch.object(pipeline.jev, "resolve", side_effect=AssertionError("client resolution")), patch.object(pipeline.jev, "JevClient", side_effect=AssertionError("client factory")), patch.object(pipeline.adaptive_research, "validate_facets", side_effect=AssertionError("facet validation")), patch.object(pipeline.PaidSourceBudget, "__init__", side_effect=AssertionError("optional budget")), patch.object(pipeline, "_adaptive_followups", side_effect=AssertionError("controller")), patch.object(pipeline.http, "post", side_effect=AssertionError("network")), patch.object(pipeline.providers, "resolve_runtime", return_value=(runtime, None)), patch.object(pipeline, "available_sources", return_value=["perplexity"]), patch.object(pipeline, "_retrieve_stream", return_value=(raw, {})) as retrieve:
        normal = pipeline.run(config={}, **base)
        skipped = pipeline.run(config=config, **base)
    assert retrieve.call_count == 2
    assert skipped.ranked_candidates == normal.ranked_candidates
    assert not {"jev_rerank", "adaptive_research", "fixed_research"} & skipped.artifacts.keys()
    assert "_perplexity_paid_budget" not in config
