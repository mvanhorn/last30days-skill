"""Jev ranks the complete shortlist or falls back without partial mutation."""
import copy
import json
from unittest.mock import Mock
import pytest
from lib import jev, rerank, schema
from tests.test_rerank_v3 import make_candidate, make_plan, FakeProvider


def candidates(n):
    result = []
    for i in range(n):
        c = make_candidate(80.0); c.candidate_id = str(i); c.title = "OpenClaw evidence"
        result.append(c)
    return result


class Judge:
    provider = "typesafe"
    def __init__(self, fail_batch=0, entity=1.0):
        self.calls = []; self.fail_batch = fail_batch; self.entity = entity; self.last_receipt = {}
    def evaluate(self, state, questions):
        self.calls.append((state, questions)); self.last_receipt = {"status": "ok", "usage": {"cost": 0.1}}
        if len(self.calls) == self.fail_batch:
            raise jev.JevError("request_failed")
        return {k: ({"type": "noul", "noul": self.entity} if q["type"] == "noul" else
                    {"type": "score", "score": 3, "confidence": 1,
                     "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1},
                     "legend": {str(i): x for i, x in enumerate(q["criteria"])}})
                for k, q in questions.items()}


def run(cs, **kw):
    opts = dict(topic="OpenClaw", plan=make_plan(), candidates=cs, provider=None, model=None, shortlist_size=len(cs))
    opts.update(kw)
    return rerank.rerank_candidates(**opts)


def test_no_key_identical_to_original_path(monkeypatch):
    post = Mock(); monkeypatch.setattr(jev.http, "post", post)
    cs = candidates(5)
    old = run(copy.deepcopy(cs))
    client, reason = jev.resolve({"LAST30DAYS_JEV_PROVIDER": "auto"})
    receipt = {}; current = run(copy.deepcopy(cs), jev_client=client, receipt=receipt)
    assert current == old and reason == "missing_key"
    assert receipt["judge_route"] == "deterministic"
    post.assert_not_called()


@pytest.mark.parametrize("count,batches", [(1, 1), (16, 1), (17, 2), (60, 4), (65, 5)])
def test_full_shortlist_bounded_batches(count, batches):
    class ScoredJudge(Judge):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for item in json.loads(state)["untrusted_candidates"]:
                # The excerpt identifies the candidate across batch-local indices.
                probability = int(item["snippet"]) / 100
                answers[f"relevance_{item['candidate']}"].update(
                    score=3 * probability,
                    probabilities={"0": 1 - probability, "1": 0, "2": 0, "3": probability},
                )
            return answers

    cs = candidates(count)
    for index, candidate in enumerate(cs):
        candidate.snippet = str(index + 1)
    expected = {str(index): index + 1 for index in range(count)}
    judge = ScoredJudge()
    receipt = {}
    result = run(cs, jev_client=judge, receipt=receipt)

    assert sorted(candidate.candidate_id for candidate in result) == sorted(expected)
    assert {candidate.candidate_id: candidate.rerank_score for candidate in result} == pytest.approx(expected)
    assert len(judge.calls) == batches and all(len(q) <= 32 for _, q in judge.calls)
    assert receipt["judge_route"] == "jev:typesafe"
    assert len(receipt["jev_calls"]) == batches


def test_second_batch_failure_discards_every_score():
    j = Judge(fail_batch=2); receipt = {}; cs = candidates(17)
    actual = run(copy.deepcopy(cs), jev_client=j, receipt=receipt)
    assert actual == run(copy.deepcopy(cs)) and len(j.calls) == 2
    assert receipt["judge_route"] == "deterministic" and receipt["fallback_reason"] == "jev_failed"


def test_failed_jev_uses_incumbent_without_partial_mutation():
    cs = candidates(17); provider = FakeProvider({"scores": [{"candidate_id": str(i), "relevance": 77} for i in range(17)]})
    receipt = {}; result = run(cs, jev_client=Judge(fail_batch=2), provider=provider, model="fixture", receipt=receipt)
    assert all(c.rerank_score == 77 for c in result) and receipt["judge_route"] == "incumbent"


@pytest.mark.parametrize("private_kind", ["source", "fused"])
def test_private_packet_never_reaches_either_hosted_judge(private_kind):
    cs = candidates(2)
    if private_kind == "source":
        cs[1].source = "corpus"
    else:
        cs[1].source_items = [schema.SourceItem(item_id="private", source="corpus", title="Private", body="Secret", url="file:///private")]
    j = Judge(); provider = Mock(); receipt = {}
    run(cs, jev_client=j, provider=provider, model="fixture", receipt=receipt)
    assert not j.calls; provider.generate_json.assert_not_called()
    assert receipt["judge_route"] == "deterministic"


def test_entity_cap_and_first_party_interaction_rules():
    cs = candidates(2)
    cs[1].source_items = [schema.SourceItem(item_id="public", source="x", title="Update", body="Hi", author="owner", url="https://x.com/owner/status/1",
                                           metadata={"mentioned_handles": ["other"]})]
    result = run(cs, jev_client=Judge(entity=0.1), resolved_handles={"owner"})
    by_id = {c.candidate_id: c for c in result}
    assert by_id["0"].rerank_score == 30 and by_id["1"].rerank_score == 100
    assert by_id["1"].metadata["interaction_targets"] == ["other"]


def test_malformed_injected_client_also_fails_closed():
    j = Judge(); j.evaluate = Mock(return_value={})
    cs = candidates(1)
    assert run(copy.deepcopy(cs), jev_client=j) == run(copy.deepcopy(cs))


def test_explicit_off_keeps_incumbent_exact_output():
    cs = candidates(3)
    provider = FakeProvider({"scores": [{"candidate_id": str(i), "relevance": 20 + 15*i} for i in range(3)]})
    expected = run(copy.deepcopy(cs), provider=provider, model="fixture")
    client, reason = jev.resolve({"LAST30DAYS_JEV_PROVIDER": "off", "TYPESAFE_API_KEY": "dummy"})
    receipt = {}
    assert run(copy.deepcopy(cs), provider=provider, model="fixture", jev_client=client, receipt=receipt) == expected
    assert reason == "off" and receipt["judge_route"] == "incumbent"


def test_oversized_packet_falls_back_without_http(monkeypatch):
    post = Mock(); monkeypatch.setattr(jev.http, "post", post)
    cs = candidates(1); cs[0].snippet = "evidence " * 10000
    receipt = {}
    assert run(copy.deepcopy(cs), jev_client=jev.JevClient("typesafe", "dummy"), receipt=receipt) == run(copy.deepcopy(cs))
    assert receipt["judge_route"] == "deterministic"
    post.assert_not_called()


def test_shortlist_tail_retains_existing_fallback_contract():
    cs = candidates(17); j = Judge(); receipt = {}
    result = run(cs, shortlist_size=16, jev_client=j, receipt=receipt)
    by_id = {c.candidate_id: c for c in result}
    assert len(j.calls) == 1 and by_id["15"].rerank_score == 100
    assert by_id["16"].explanation.startswith("fallback-local-score")


def test_oversized_injected_number_uses_unmodified_fallback():
    cs = candidates(1)
    judge = Judge(entity=10 ** 1000)
    receipt = {}
    expected = run(copy.deepcopy(cs))
    assert run(copy.deepcopy(cs), jev_client=judge, receipt=receipt) == expected
    assert receipt["judge_route"] == "deterministic"
    assert receipt["fallback_reason"] == "jev_failed"
