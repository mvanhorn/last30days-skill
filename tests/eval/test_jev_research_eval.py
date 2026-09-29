"""Independent ranking oracles, disclosure, and live-failure controls."""
import copy
import json
from dataclasses import asdict
from unittest.mock import patch

import pytest

import evaluate_jev_research as evaluator


@pytest.fixture
def packet():
    return evaluator.load_packet(evaluator.DEFAULT_FIXTURE)


def find(packet, case_id):
    return next(c for c in packet['cases'] if c['id'] == case_id)


def test_topic_disjoint_labels_and_transport_inputs(packet):
    calibration = {c['topic_family'] for c in packet['cases'] if c['split'] == 'calibration'}
    holdout = {c['topic_family'] for c in packet['cases'] if c['split'] == 'holdout'}
    assert calibration.isdisjoint(holdout)
    for case in packet['cases']:
        plan, candidates = evaluator.prepare(case)
        wire = json.dumps([asdict(c) for c in candidates])
        for forbidden in ('supported_facets', 'evidence_role', 'expected_controller_action', case['id']):
            assert forbidden not in wire
        assert plan.raw_topic == case['topic']


def test_collision_ranking_oracle_and_wrong_order_mutation(packet):
    case = find(packet, 'hold-mercury')
    good = evaluator.metrics(case, ['candidate-2', 'candidate-3', 'candidate-5', 'candidate-1', 'candidate-4'])
    bad = evaluator.metrics(case, ['candidate-1', 'candidate-4', 'candidate-5', 'candidate-2', 'candidate-3'])
    assert good['ndcg_at_5'] == 1
    assert good['precision_at_5'] == 0.4
    assert good['recall_at_5'] == 1
    assert good['facet_coverage_at_5'] == 1
    assert bad['ndcg_at_5'] < 0.7  # Planet/music first must visibly lose.


def test_duplicate_announcement_never_fills_experience_facet(packet):
    case = find(packet, 'hold-atlas')
    observed = evaluator.metrics(case, [d['id'] for d in case['candidates']])
    assert observed['covered_facets'] == ['release']
    assert observed['facet_coverage_at_5'] == 0.5
    assert observed['precision_at_5'] == 0.2  # Duplicate source counts once.
    assert observed['relevant_source_denominator'] == 1


def test_prepared_candidates_preserve_date_window_classification(packet):
    _, candidates = evaluator.prepare(find(packet, 'hold-atlas'))
    assert evaluator.schema.candidate_out_of_window(candidates[3])
    assert not evaluator.schema.candidate_out_of_window(candidates[0])


def test_fixture_rejects_split_leakage_and_private_source(packet, tmp_path):
    path = tmp_path / 'bad.json'
    bad = copy.deepcopy(packet)
    bad['cases'][-1]['topic_family'] = bad['cases'][0]['topic_family']
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='topic_split_leakage'):
        evaluator.load_packet(path)
    bad = copy.deepcopy(packet)
    bad['cases'][0]['candidates'][0]['source'] = 'corpus'
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='public_shortlist'):
        evaluator.load_packet(path)


def test_failed_jev_does_not_pass_on_deterministic_fallback(packet):
    case = find(packet, 'hold-lumen')
    def fallback(**kwargs):
        kwargs['receipt'].update(judge_route='deterministic', fallback_reason='provider_error')
        return kwargs['candidates']
    with patch.object(evaluator.rerank, 'rerank_candidates', side_effect=fallback):
        row = evaluator.evaluate_case(case, 'jev', jev_client=object())
    assert row['status'] == 'error'
    assert row['metrics']['ndcg_at_5'] == 0
    assert evaluator.summarize([row])['jev']['metrics']['ndcg_at_5']['denominator'] == 1


def test_empty_and_unavailable_are_distinct_without_judge_calls(packet):
    with patch.object(evaluator.rerank, 'rerank_candidates') as rank:
        empty = evaluator.evaluate_case(find(packet, 'hold-nimbus'), 'deterministic')
        unavailable = evaluator.evaluate_case(find(packet, 'hold-polaris'), 'deterministic')
    rank.assert_not_called()
    assert empty['collector_status'] == 'ok'
    assert unavailable['collector_status'] == 'unauthorized'
    assert empty['status'] == unavailable['status'] == 'no_candidates'


def test_offline_runner_is_real_baseline_and_never_reads_credentials(tmp_path):
    output = tmp_path / 'offline.json'
    with patch.object(evaluator.env, 'get_config') as config, patch.object(evaluator.http, '_open_request') as remote:
        assert evaluator.main(['--output', str(output), '--limit', '1']) == 0
    config.assert_not_called()
    remote.assert_not_called()
    report = json.loads(output.read_text())
    assert report['mode'] == 'offline'
    assert report['remote_attempts'] == 0
    assert set(report['summary']) == {'deterministic'}
    assert report['cases'][0]['ranking']
    assert report['cost_usd'] == 0


def test_live_no_credentials_fails_without_any_calls(tmp_path):
    output = tmp_path / 'no-keys.json'
    with patch.object(evaluator.env, 'get_config', return_value={}), patch.object(evaluator.http, '_open_request') as remote:
        result = evaluator.main(['--mode', 'live', '--execute', '--max-calls', '2', '--limit', '1', '--output', str(output)])
    assert result == 2
    remote.assert_not_called()
    assert json.loads(output.read_text())['status'] == 'blocked_provider_unavailable'


def test_live_small_budget_fails_before_config_or_calls(tmp_path):
    output = tmp_path / 'no-budget.json'
    with patch.object(evaluator.env, 'get_config') as config:
        assert evaluator.main(['--mode', 'live', '--execute', '--max-calls', '1', '--limit', '1', '--output', str(output)]) == 2
    config.assert_not_called()
    assert json.loads(output.read_text())['status'] == 'blocked_attempt_budget'


def test_transport_errors_are_recorded_without_secret_message():
    with patch.object(evaluator.http, '_open_request', side_effect=OSError('secret-do-not-record')):
        meter = evaluator.Meter(1, evaluator.time.monotonic() + 10)
    with pytest.raises(OSError):
        meter.open_request(object(), 1)
    with pytest.raises(RuntimeError):
        meter.open_request(object(), 1)
    assert len(meter.attempts) == 1
    assert meter.attempts[0]['status'] == 'error'
    assert 'secret-do-not-record' not in json.dumps(meter.attempts)


def test_existing_output_is_not_overwritten_or_executed(tmp_path):
    output = tmp_path / 'exists.json'
    output.write_text('keep')
    with patch.object(evaluator.env, 'get_config') as config:
        assert evaluator.main(['--mode', 'live', '--execute', '--max-calls', '2', '--limit', '1', '--output', str(output)]) == 2
    config.assert_not_called()
    assert output.read_text() == 'keep'


def test_incumbent_partial_response_cannot_be_counted_as_live_model_success(packet):
    class IncompleteProvider:
        name = "fixture-provider"
        def generate_json(self, *args, **kwargs):
            return {"scores": []}
    row = evaluator.evaluate_case(find(packet, 'hold-mercury'), 'incumbent', IncompleteProvider(), 'test-model')
    assert row['status'] == 'error'
    assert row['metrics']['ndcg_at_5'] == 0
    assert row['requested_model'] == 'test-model'


@pytest.mark.parametrize("code,outcome", [(401, "auth-failed"), (402, "payment-required"),
                                          (429, "rate-limited"), (504, "timeout"), (422, "error")])
def test_status_and_outcome_survive_both_http_error_layers_without_body(code, outcome):
    from urllib.error import HTTPError
    secret = "private-body-token-never-log"
    native = HTTPError("https://example.com/" + secret, code, secret, {"secret": secret}, None)
    wrapped = evaluator.http.HTTPError(secret, status_code=code, body=secret,
                                       outcome_state=outcome)
    with patch.object(evaluator.http, '_open_request', side_effect=native), \
         patch.object(evaluator.http, 'post', side_effect=wrapped):
        meter = evaluator.Meter(2, evaluator.time.monotonic() + 10)
    with pytest.raises(HTTPError):
        meter.open_request(object(), 1)
    with pytest.raises(evaluator.http.HTTPError):
        meter.post("https://example.com/" + secret, {"model": "jev-1.13"})
    for row in (meter.attempts[0], meter.calls[0]):
        assert row['status_code'] == code
        assert row['outcome_state'] == outcome
        assert secret not in json.dumps(row)


def test_safe_failure_rejects_arbitrary_outcome_and_status_text():
    failure = evaluator.http.HTTPError('secret', status_code='secret', outcome_state='secret')
    assert evaluator.safe_failure(failure) == {'status_code': None, 'outcome_state': 'error'}
    assert evaluator.safe_failure(TimeoutError('secret')) == {'status_code': None, 'outcome_state': 'timeout'}


@pytest.mark.parametrize("provider,wire_model", [("typesafe", "jev-1.13.0"),
                                               ("openrouter", "typesafe/jev-1.13")])
def test_jev_requested_identity_comes_from_selected_client(packet, provider, wire_model):
    from types import SimpleNamespace
    client = SimpleNamespace(provider=provider, model=wire_model)
    # Empty evidence makes no request, but must still identify the selected arm exactly.
    row = evaluator.evaluate_case(find(packet, 'hold-nimbus'), 'jev', jev_client=client)
    assert row['requested_model'] == wire_model
    assert row['provider'] == provider
    assert row['status'] == 'no_candidates'
