"""Fixed follow-up contracts, using offline transport fixtures only."""
import json
import sys
from unittest.mock import patch

import pytest
import last30days as cli
from lib import adaptive_research, health, pipeline, schema
from tests.test_adaptive_pipeline import FACETS, RANGE, TOPIC, answers_for, item, packet


def fixed_packet():
    kwargs = packet()
    kwargs['config']['_research_policy'] = 'fixed'
    return kwargs


@pytest.mark.parametrize('policy', ['fixed', 'adaptive'])
def test_cli_policy_requires_facets(policy):
    args = cli.build_parser().parse_args(['topic', '--research-policy', policy])
    with pytest.raises(ValueError, match='requires --research-facets'):
        cli._configure_jev_research(args, 'topic', {})


@pytest.mark.parametrize('policy', [None, 'fixed', 'adaptive'])
def test_cli_policy_is_explicit_and_keeps_facet_search_types(tmp_path, policy):
    path = tmp_path / 'facets.json'
    path.write_text(json.dumps(FACETS))
    argv = ['topic', '--jev-provider', 'auto', '--research-facets', str(path), '--perplexity-search-type', 'fast']
    if policy: argv += ['--research-policy', policy]
    config = {'TYPESAFE_API_KEY': 'dummy-key'}
    cli._configure_jev_research(cli.build_parser().parse_args(argv), 'topic', config)
    assert config.get('_research_policy') == policy
    assert [f['search_type'] for f in config['_research_facets']] == ['fast', 'web']


@pytest.mark.parametrize('argv', [['--welcome'], ['setup', '--store-key', 'TYPESAFE_API_KEY']])
def test_early_dispatch_rejects_policy_without_side_effects(monkeypatch, argv):
    monkeypatch.setattr(sys, 'argv', ['last30days.py', *argv, '--research-policy', 'fixed'])
    with patch.object(cli, '_run_store_key') as store, patch.object(cli.env, 'get_config') as config:
        assert cli.main() == 2
    store.assert_not_called(); config.assert_not_called()


@pytest.mark.parametrize('config', [{'_research_policy': 'fixed'}, {'_research_policy': 'invalid', '_research_facets': FACETS}])
def test_pipeline_rejects_bad_policy_before_provider_resolution(config):
    with patch.object(pipeline.jev, 'resolve') as resolve:
        with pytest.raises(ValueError, match='Research policy'):
            pipeline.run(topic=TOPIC, config=config, depth="quick", mock=True)
    resolve.assert_not_called()


@pytest.mark.parametrize('condition,reason', [
    ('private', 'private_candidates'), ('no_key', 'judge_unavailable'), ('mock', 'mock'),
    ('source_off', 'native_search_not_enabled'), ('native_key_missing', 'native_search_not_enabled'),
    ('budget_zero', 'source_budget_disabled'), ('incomplete', 'collection_incomplete'),
    ('judge_failed', 'judge_failed'), ('timeout', 'deadline_exceeded'),
])
def test_fixed_guards_before_any_extra_http(condition, reason):
    kwargs = fixed_packet()
    if condition == 'private': kwargs['candidates'][0].sources.append('corpus')
    if condition == 'no_key': kwargs['client'] = None
    if condition == 'mock': kwargs['mock'] = True
    if condition == 'source_off': kwargs['available'] = []
    if condition == 'native_key_missing': kwargs['config'].pop('PERPLEXITY_API_KEY')
    if condition == 'budget_zero': kwargs['config']['_max_source_fetches'] = 0
    if condition == 'incomplete': kwargs['bundle'].source_status['reddit'] = schema.SourceOutcome('reddit', health.TIMEOUT)
    if condition == 'judge_failed': kwargs['judge_receipt'] = {'judge_route': 'deterministic'}
    if condition == 'timeout': kwargs['run_started'] -= 301
    with patch.object(adaptive_research, 'assess') as judge, patch.object(pipeline, '_retrieve_stream') as search:
        assert pipeline._adaptive_followups(**kwargs) is False
    judge.assert_not_called(); search.assert_not_called()
    receipt = kwargs['bundle'].artifacts['fixed_research']
    assert receipt['reason'] == reason
    assert not {'before', 'after', 'facets'} & receipt.keys()


def test_fixed_uses_two_distinct_unused_queries_in_order_without_judging():
    kwargs = fixed_packet()
    kwargs['facets'] = [dict(FACETS[0], query='  ' + TOPIC.upper() + ' '), FACETS[0],
                        dict(FACETS[0], id='duplicate', query=FACETS[0]['query'].upper()), FACETS[1]]
    rows = [dict(id=str(i), title='Atlas coding tool user report', url=f'https://example.com/new-{i}',
                 snippet='Atlas coding tool user report about failures', date='2026-09-23') for i in range(2)]
    with patch.object(adaptive_research, 'assess') as judge, patch.object(pipeline, '_retrieve_stream', side_effect=[([r], {}) for r in rows]) as search:
        assert pipeline._adaptive_followups(**kwargs)
    judge.assert_not_called()
    assert [c.kwargs['subquery'].search_query for c in search.call_args_list] == [f['query'] for f in FACETS]
    assert [c.kwargs['config']['LAST30DAYS_PERPLEXITY_SEARCH_TYPE'] for c in search.call_args_list] == ['fast', 'web']
    assert all(c.kwargs['date_range'] == RANGE for c in search.call_args_list)
    assert kwargs['config']['_perplexity_paid_budget'].used == 3
    receipt = kwargs['bundle'].artifacts['fixed_research']
    assert receipt['status'] == 'complete'
    assert not {'before', 'after', 'facets'} & receipt.keys()


@pytest.mark.parametrize('result', ['empty', 'duplicate', 'failed', 'exhausted'])
def test_fixed_stops_on_no_new_url_failure_or_budget(result):
    kwargs = fixed_packet()
    raw = [] if result != 'duplicate' else [dict(id='dup', title=item().title, url=item().url, snippet=item().snippet, date='2026-09-20')]
    if result == 'exhausted': kwargs['config']['_perplexity_paid_budget'].used = 3
    with patch.object(pipeline, '_retrieve_stream', return_value=(raw, {}),
                      side_effect=TimeoutError('fixture') if result == 'failed' else None) as search:
        assert not pipeline._adaptive_followups(**kwargs)
    assert search.call_count == (0 if result == 'exhausted' else 1)
    receipt = kwargs['bundle'].artifacts['fixed_research']
    assert receipt['reason'] == {'empty': 'no_new_evidence', 'duplicate': 'no_new_evidence', 'failed': 'search_failed', 'exhausted': 'search_budget_exhausted'}[result]
    assert not {'before', 'after', 'facets'} & receipt.keys()


def test_fixed_pipeline_reranks_before_and_after_without_coverage_or_private_input(tmp_path, monkeypatch):
    private_dir = tmp_path / 'notes'; private_dir.mkdir()
    (private_dir / 'private.md').write_text('Atlas coding tool user experience: PRIVATE_FIXED_CANARY local observation.')
    monkeypatch.setattr(pipeline.env, 'CONFIG_DIR', tmp_path / 'config')
    calls = []
    def post(url, payload, **kwargs):
        calls.append((url, payload))
        if 'systemone' in url:
            assert all(k.startswith(('relevance_', 'entity_')) for k in payload['questions'])
            return {'model': 'jev-1.13', 'answers': answers_for(payload['questions']), 'usage': {'input_tokens': 100}}
        assert url == 'https://api.perplexity.ai/search'
        followup = payload['query'] != TOPIC
        return {'results': [{'id': 'followup' if followup else 'release',
            'title': 'Atlas coding tool user failure' if followup else item().title,
            'url': 'https://example.com/new-public' if followup else item().url,
            'snippet': 'Atlas coding tool user failure after retry.' if followup else item().snippet,
            'date': '2026-09-24'}]}
    plan = {'intent': 'product', 'freshness_mode': 'balanced_recent', 'cluster_mode': 'theme',
            'subqueries': [{'label': 'primary', 'search_query': TOPIC, 'ranking_query': TOPIC,
                           'sources': ['perplexity'], 'weight': 1.0}]}
    with patch.object(pipeline, 'available_sources', return_value=['perplexity']), patch.object(pipeline.http, 'post', side_effect=post), patch.object(adaptive_research, 'assess') as assess:
        report = pipeline.run(topic=TOPIC, depth='quick', mock=False, web_backend='none',
            requested_sources=['perplexity'], external_plan=plan, as_of_date=RANGE[1],
            corpus_dirs=[str(private_dir)], corpus_all_time=True,
            config={'PERPLEXITY_API_KEY': 'dummy-pplx', 'TYPESAFE_API_KEY': 'dummy-jev',
                    'LAST30DAYS_JEV_PROVIDER': 'typesafe', 'LAST30DAYS_PERPLEXITY_MODE': 'search',
                    'LAST30DAYS_PERPLEXITY_SEARCH_TYPE': 'fast', '_research_facets': FACETS[:1], '_research_policy': 'fixed'})
    assess.assert_not_called()
    assert 'PRIVATE_FIXED_CANARY' not in json.dumps(calls)
    assert any(c.source == 'corpus' for c in report.ranked_candidates)
    assert any(c.url == 'https://example.com/new-public' for c in report.ranked_candidates)
    assert len([1 for url, _ in calls if url.endswith('/search')]) == 2
    assert len([1 for url, _ in calls if 'systemone' in url]) == 2
    assert report.artifacts['jev_rerank']['initial']['judge_route'] == 'jev:typesafe'
    assert report.artifacts['jev_rerank']['final']['judge_route'] == 'jev:typesafe'
    assert report.artifacts['fixed_research']['status'] == 'complete'
    assert 'adaptive_research' not in report.artifacts


@pytest.mark.parametrize('policy', ['fixed', 'adaptive'])
@pytest.mark.parametrize('cap,expected_calls', [(1, 0), (2, 1), (3, 2), (9, 2), (-1, 0), (0, 0)])
def test_followup_source_cap_includes_initial_request_for_both_policies(policy, cap, expected_calls):
    kwargs = packet()
    kwargs['config'].update(_research_policy=policy, _max_source_fetches=cap)
    raw = [dict(id=str(i), title='Atlas coding tool user report', url=f'https://example.com/capped-{i}',
                snippet='Atlas coding tool user report about failures', date='2026-09-23') for i in range(2)]
    with patch.object(pipeline, '_retrieve_stream', side_effect=[([r], {}) for r in raw]) as search, patch.object(kwargs['client'], 'evaluate', wraps=kwargs['client'].evaluate) as judge:
        added = pipeline._adaptive_followups(**kwargs)
    assert search.call_count == expected_calls
    assert added == bool(expected_calls)
    assert kwargs['config']['_perplexity_paid_budget'].used == 1 + expected_calls
    if cap <= 0:
        judge.assert_not_called()
        assert kwargs['bundle'].artifacts[f'{policy}_research']['reason'] == 'source_budget_disabled'
    elif cap < 3:
        assert kwargs['bundle'].artifacts[f'{policy}_research']['reason'] == 'search_budget_exhausted'


@pytest.mark.parametrize("error", [AssertionError("local defect"), ValueError("local defect")])
def test_followup_normalization_fault_is_not_a_source_failure(error):
    kwargs = fixed_packet()
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})), \
         patch.object(pipeline, "_normalize_score_dedupe", side_effect=error):
        with pytest.raises(type(error), match="local defect"):
            pipeline._adaptive_followups(**kwargs)
    receipt = kwargs["bundle"].artifacts["fixed_research"]
    assert "failure_state" not in receipt["searches"][0]


def test_followup_bundle_fault_is_not_a_source_failure():
    kwargs = fixed_packet()
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})), \
         patch.object(kwargs["bundle"], "add_items", side_effect=RuntimeError("local defect")):
        with pytest.raises(RuntimeError, match="local defect"):
            pipeline._adaptive_followups(**kwargs)
    receipt = kwargs["bundle"].artifacts["fixed_research"]
    assert "failure_state" not in receipt["searches"][0]
