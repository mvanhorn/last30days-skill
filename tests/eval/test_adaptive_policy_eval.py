"""Controller policy evaluation with independent fixture labels, not Jev quality.

The controlled judge translates author labels into typed responses. It is an
oracle for policy wiring only; no model, transport quality, or latency is scored.
"""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import evaluate_jev_research as evaluator
from lib import adaptive_research, health, pipeline, rerank, schema

PACKET = evaluator.load_packet(Path(__file__).resolve().parents[2] / "fixtures/jev_eval.json")


class AuthorLabelJudge:
    provider = "controlled-label-oracle"

    def __init__(self, case):
        self.case = case
        self.calls = []
        self.last_receipt = {}
        self.by_title = {doc["title"]: case["labels"][doc["id"]] for doc in case["candidates"]}

    def evaluate(self, state, questions):
        docs = json.loads(state)["untrusted_candidates"]
        self.calls.append((state, questions))
        self.last_receipt = {"status": "ok", "provider": "controlled-label-oracle", "usage": {}}
        answers = {}
        for key, question in questions.items():
            parts = key.split("_")
            label = self.by_title[docs[int(parts[1])]["title"]]
            if parts[0] == "role":
                role = {"direct_experience": "experience", "announcement": "announcement",
                        "repost": "announcement", "promotion": "promotion", "analysis": "analysis"}.get(label["evidence_role"], "unclear")
                answers[key] = {"type": "choice", "choice": role, "confidence": 1,
                                "probabilities": {r: float(r == role) for r in question["criteria"]}}
            else:
                facet = self.case["facets"][int(parts[2])]
                answers[key] = {"type": "noul", "noul": float(facet in label["supported_facets"])}
        return answers


def host_facets(case):
    return [{"id": f, "question": f"What does this excerpt show about {f}?",
             "query": f"{case['topic']} {f}",
             "required_role": "experience" if f == "experience" else "any"}
            for f in case["facets"]]


@pytest.mark.parametrize("case", PACKET["cases"], ids=lambda c: c["id"])
def test_controller_actions_from_independent_author_labels(case):
    plan, candidates = evaluator.prepare(case)
    judge = AuthorLabelJudge(case)
    bundle = schema.RetrievalBundle()
    bundle.source_status["grounding"] = schema.SourceOutcome(
        "grounding", health.OK if case["collector_status"] == "ok" else health.AUTH_FAILED,
        items_returned=len(candidates))
    # An empty successful packet has no rerank request. Exercise that real path.
    initial_receipt = {"judge_route": "jev:controlled-label-oracle"}
    if not candidates:
        rerank.rerank_candidates(topic=case["topic"], plan=plan, candidates=[], provider=None,
                                 model=None, shortlist_size=16, jev_client=judge, receipt=initial_receipt)
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        pipeline._adaptive_followups(
            topic=case["topic"], config={"PERPLEXITY_API_KEY": "dummy"},
            facets=host_facets(case), client=judge, judge_receipt=initial_receipt,
            candidates=candidates, bundle=bundle, plan=plan, available=["perplexity"],
            runtime=None, depth="default", date_range=(case["range_from"], case["range_to"]),
            run_started=time.monotonic(), per_stream_limit=10)
    result = bundle.artifacts["adaptive_research"]
    expected = case["expected_controller_action"]
    if expected == "target_experience":
        assert retrieve.call_count == 1
        assert result["searches"][0]["facet_id"] == "experience"
        assert result["before"]["facets"][-1]["state"] == "gap"
    elif expected == "report_collector_gap":
        assert retrieve.call_count == 0 and not judge.calls
        assert result["reason"] == "collection_incomplete"
    else:
        assert retrieve.call_count == 0
        assert all(f["state"] == "covered" for f in result["before"]["facets"])
        if expected == "preserve_contradiction":
            failure = next(f for f in result["before"]["facets"] if f["id"] == "failure")
            assert {"candidate-1", "candidate-2"} <= set(failure["evidence_ids"])
        else:
            assert expected == "stop"


def test_controlled_policy_never_sends_labels_in_evidence():
    case = PACKET["cases"][0]
    _, candidates = evaluator.prepare(case)
    judge = AuthorLabelJudge(case)
    adaptive_research.assess(candidates, host_facets(case), judge)
    state = json.loads(judge.calls[0][0])
    assert set(state) == {"untrusted_candidates"}
    assert all(set(doc) == {"candidate", "title", "snippet"} for doc in state["untrusted_candidates"])
