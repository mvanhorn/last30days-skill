#!/usr/bin/env python3
"""Frozen public/synthetic packets: production deterministic, incumbent, and Jev rerank."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import re
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from lib import env, http, providers, relevance, rerank, schema

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = ROOT / "fixtures" / "jev_eval.json"
ARMS = {"deterministic", "incumbent", "jev"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_packet(path: Path) -> dict:
    packet = json.loads(path.read_text())
    if packet.get("schema_version") != "jev-research-eval/1":
        raise ValueError("unsupported_fixture_schema")
    cases = packet.get("cases") or []
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("empty_or_duplicate_cases")
    families = {split: {c["topic_family"] for c in cases if c["split"] == split}
                for split in ("calibration", "holdout")}
    if families["calibration"] & families["holdout"]:
        raise ValueError("topic_split_leakage")
    for case in cases:
        if case["split"] not in families:
            raise ValueError("unknown_split")
        docs = case["candidates"]
        ids = [d["id"] for d in docs]
        if len(ids) != len(set(ids)) or set(ids) != set(case["labels"]):
            raise ValueError("candidate_label_identity_mismatch")
        if len(docs) > 16 or any(d["source"] == "corpus" for d in docs):
            raise ValueError("public_shortlist_only_max16")
        for label in case["labels"].values():
            if type(label["relevance"]) is not int or label["relevance"] not in range(4):
                raise ValueError("invalid_relevance_label")
            if not set(label["supported_facets"]) <= set(case["facets"]):
                raise ValueError("unknown_facet_label")
    return packet


def prepare(case: dict) -> tuple[schema.QueryPlan, list[schema.Candidate]]:
    """Allowlist only public inputs. Gold labels and expected controller actions stay outside."""
    plan = schema.QueryPlan(case["intent"], "balanced_recent", "theme", case["topic"],
                            [schema.SubQuery("primary", case["topic"], case["topic"], ["grounding"])],
                            {"grounding": 1.0})
    candidates = []
    for index, doc in enumerate(case["candidates"]):
        item = schema.SourceItem(doc["id"], doc["source"], doc["title"], doc["text"], doc["url"],
                                 published_at=doc.get("published_at"), date_confidence="high")
        in_window = bool(doc.get("published_at") and case["range_from"] <= doc["published_at"][:10] <= case["range_to"])
        candidates.append(schema.Candidate(
            candidate_id=doc["id"], item_id=doc["id"], source=doc["source"], title=doc["title"],
            url=doc["url"], snippet=doc["text"], subquery_labels=["primary"],
            native_ranks={"grounding": index + 1},
            local_relevance=relevance.token_overlap_relevance(case["topic"], doc["title"] + " " + doc["text"]),
            freshness=100 if in_window else 0, engagement=0, source_quality=0.5,
            rrf_score=1 / (60 + index + 1), sources=[doc["source"]], source_items=[item],
            metadata={"range_from": case["range_from"], "range_to": case["range_to"]},
        ))
    return plan, candidates


def metrics(case: dict, ranking: list[str], cutoff: int = 5) -> dict:
    labels = case["labels"]
    urls = {d["id"]: d["url"] for d in case["candidates"]}
    seen = set()
    grades = []
    facets = set()
    relevant_urls = {urls[k] for k, v in labels.items() if v["relevance"] >= 2}
    selected_relevant = set()
    for cid in ranking[:cutoff]:
        if cid not in labels:
            raise ValueError("ranking_unknown_candidate")
        repeated = urls[cid] in seen
        seen.add(urls[cid])
        grade = 0 if repeated else labels[cid]["relevance"]
        grades.append(grade)
        if grade >= 2:
            selected_relevant.add(urls[cid])
            facets.update(labels[cid]["supported_facets"])
    best_by_url: dict[str, int] = {}
    for cid, label in labels.items():
        best_by_url[urls[cid]] = max(best_by_url.get(urls[cid], 0), label["relevance"])
    ideal = sorted(best_by_url.values(), reverse=True)[:cutoff]
    dcg = lambda values: sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(values))
    idcg = dcg(ideal)
    return {"ndcg_at_5": dcg(grades) / idcg if idcg else None,
            "precision_at_5": sum(g >= 2 for g in grades) / cutoff,
            "recall_at_5": len(selected_relevant) / len(relevant_urls) if relevant_urls else None,
            "facet_coverage_at_5": len(facets) / len(case["facets"]) if case["facets"] else None,
            "covered_facets": sorted(facets), "relevant_source_denominator": len(relevant_urls)}


def safe_model(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", value) else None


def safe_usage(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    keys = ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
            "promptTokenCount", "candidatesTokenCount", "totalTokenCount", "cost")
    return {k: value[k] for k in keys if type(value.get(k)) in (int, float)
            and math.isfinite(value[k]) and value[k] >= 0}


def safe_failure(exc: Exception) -> dict:
    """Keep numeric status and a fixed outcome vocabulary; never inspect exception text."""
    code = getattr(exc, "status_code", None)
    if type(code) is not int:
        code = getattr(exc, "code", None)
    code = code if type(code) is int and 100 <= code <= 599 else None
    allowed = {"error", "timeout", "unreachable", "auth-failed", "payment-required",
               "rate-limited", "schema-drift"}
    outcome = getattr(exc, "outcome_state", None)
    if outcome not in allowed:
        if code == 504 or isinstance(exc, TimeoutError):
            outcome = "timeout"
        elif code is not None:
            outcome = http.classify_failure(status_code=code)
        elif isinstance(exc, OSError):
            outcome = "unreachable"
        else:
            outcome = "error"
    return {"status_code": code, "outcome_state": outcome}


class Meter:
    """Sequential evaluator instrumentation. Never records headers, keys, response text, or URLs."""
    def __init__(self, max_calls: int, deadline: float):
        self.max_calls, self.deadline = max_calls, deadline
        self.calls: list[dict] = []
        self.attempts: list[dict] = []
        self._post = http.post
        self._open = http._open_request

    def open_request(self, req, timeout):
        if len(self.attempts) >= self.max_calls or time.monotonic() >= self.deadline:
            raise RuntimeError("eval_attempt_budget_exhausted")
        row = {"attempt": len(self.attempts) + 1, "status": "started"}
        self.attempts.append(row)
        started = time.perf_counter()
        try:
            result = self._open(req, min(timeout, max(0.1, self.deadline - time.monotonic())))
            code = getattr(result, "status", None)
            code = code if type(code) is int and 100 <= code <= 599 else None
            row.update(status="response", http_status=code, status_code=code, outcome_state="ok")
            return result
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__, **safe_failure(exc))
            raise
        finally:
            row["open_latency_ms"] = (time.perf_counter() - started) * 1000

    def post(self, url, json_data, headers=None, **kwargs):
        if time.monotonic() >= self.deadline:
            raise RuntimeError("eval_deadline_exhausted")
        row = {"request_sha256": digest(json_data), "requested_model": safe_model(json_data.get("model")),
               "status": "started", "model": None, "usage": {}, "cost_usd": None,
               "first_attempt_index": len(self.attempts)}
        self.calls.append(row)
        started = time.perf_counter()
        # Incumbent clients normally retry; the eval uses a strict one-attempt budget.
        kwargs.update(retries=1, max_429_retries=0, retry_dns=False,
                      deadline_monotonic=min(kwargs.get("deadline_monotonic") or self.deadline, self.deadline))
        try:
            body = self._post(url, json_data, headers=headers, **kwargs)
            usage = safe_usage(body.get("usage") or body.get("usageMetadata"))
            row.update(status="ok", model=safe_model(body.get("model") or body.get("modelVersion")),
                       usage=usage, cost_usd=usage.get("cost"), outcome_state="ok",
                       status_code=self.attempts[-1].get("status_code")
                       if len(self.attempts) > row["first_attempt_index"] else None)
            return body
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__, **safe_failure(exc))
            raise
        finally:
            row.update(latency_ms=(time.perf_counter() - started) * 1000,
                       attempt_count=len(self.attempts) - row["first_attempt_index"])

    @contextlib.contextmanager
    def active(self):
        with patch.object(http, "post", self.post), patch.object(http, "_open_request", self.open_request), \
             patch.object(http, "MIN_DNS_RETRIES", 1):
            yield


class StrictIncumbent:
    """Use the incumbent request unchanged; expose silent per-row local scoring as a failed arm."""
    def __init__(self, provider, ids):
        self.provider, self.ids = provider, set(ids)

    def generate_json(self, *args, **kwargs):
        result = self.provider.generate_json(*args, **kwargs)
        rows = result.get("scores") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != len(self.ids):
            raise ValueError("incomplete_incumbent_response")
        ids = [row.get("candidate_id") for row in rows if isinstance(row, dict)]
        if len(ids) != len(self.ids) or set(ids) != self.ids:
            raise ValueError("invalid_incumbent_identity")
        for row in rows:
            score = row.get("relevance")
            if isinstance(score, bool) or not isinstance(score, (str, int, float)):
                raise ValueError("invalid_incumbent_score")
            if not math.isfinite(float(score)) or not 0 <= float(score) <= 100:
                raise ValueError("invalid_incumbent_score")
        return result


def evaluate_case(case: dict, arm: str, provider=None, model=None, jev_client=None) -> dict:
    plan, candidates = prepare(case)
    row = {"case_id": case["id"], "split": case["split"], "arm": arm, "case_sha256": digest(case),
           "input_sha256": digest({"topic": case["topic"], "candidates": case["candidates"]}),
           "collector_status": case["collector_status"], "status": "started",
           "requested_model": model if arm == "incumbent" else (safe_model(getattr(jev_client, "model", None)) if arm == "jev" else "deterministic"),
           "provider": getattr(provider, "name", None) if arm == "incumbent" else (getattr(jev_client, "provider", None) if arm == "jev" else "local")}
    receipt: dict = {}
    started = time.perf_counter()
    if not candidates:
        row.update(status="no_candidates", ranking=[], metrics=metrics(case, []), remote_judge_expected=False)
    else:
        try:
            # Production fallback stderr can contain upstream bodies. Do not emit it in eval logs.
            with contextlib.redirect_stderr(io.StringIO()):
                result = rerank.rerank_candidates(topic=case["topic"], plan=plan, candidates=candidates,
                    provider=StrictIncumbent(provider, [c.candidate_id for c in candidates]) if arm == "incumbent" and provider else None,
                    model=model if arm == "incumbent" else None,
                    shortlist_size=len(candidates), jev_client=jev_client if arm == "jev" else None,
                    receipt=receipt)
            route = receipt.get("judge_route")
            if arm == "jev" and not str(route).startswith("jev:"):
                raise RuntimeError("jev_did_not_execute")
            if arm == "incumbent" and (provider is None or route != "incumbent" or receipt.get("fallback_reason")):
                raise RuntimeError("incumbent_did_not_execute")
            ranking = [c.candidate_id for c in result]
            row.update(status="ok", ranking=ranking, metrics=metrics(case, ranking),
                       scores={c.candidate_id: c.final_score for c in result}, judge_route=route,
                       fallback_reason=receipt.get("fallback_reason"), remote_judge_expected=arm != "deterministic")
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__, ranking=[], metrics=metrics(case, []),
                       judge_route=receipt.get("judge_route"), fallback_reason=receipt.get("fallback_reason"),
                       remote_judge_expected=arm != "deterministic")
    row["wall_ms"] = (time.perf_counter() - started) * 1000
    return row


def summarize(rows: list[dict]) -> dict:
    result = {}
    for arm in sorted({r["arm"] for r in rows}):
        subset = [r for r in rows if r["arm"] == arm]
        values = {}
        for key in ("ndcg_at_5", "precision_at_5", "recall_at_5", "facet_coverage_at_5"):
            valid = [r["metrics"][key] for r in subset if r["metrics"][key] is not None]
            values[key] = {"mean": sum(valid) / len(valid) if valid else None, "denominator": len(valid)}
        result[arm] = {"cases": len(subset), "errors": sum(r["status"] == "error" for r in subset),
                       "empty_cases": sum(r["status"] == "no_candidates" for r in subset), "metrics": values}
    return result


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--mode", choices=["offline", "live"], default="offline")
    p.add_argument("--arms", default=None)
    p.add_argument("--split", choices=["calibration", "holdout", "all"], default="holdout")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--jev-provider", choices=["auto", "typesafe", "openrouter"], default="auto")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--max-calls", type=int, default=0)
    p.add_argument("--timeout", type=float, default=120)
    args = p.parse_args(argv)
    args.arms = (args.arms or ("deterministic" if args.mode == "offline" else "deterministic,incumbent,jev")).split(",")
    if not set(args.arms) <= ARMS or len(set(args.arms)) != len(args.arms) or not 1 <= args.limit <= 100:
        p.error("use unique supported arms and limit 1..100")
    if not 0 < args.timeout <= 600 or args.max_calls < 0:
        p.error("timeout must be 0..600 seconds; max-calls must be nonnegative")
    if args.mode == "offline" and args.arms != ["deterministic"]:
        p.error("offline mode supports only the real deterministic baseline; no simulated Jev scores")
    if args.mode == "live" and (not args.execute or args.max_calls < 1):
        p.error("live mode requires --execute and a positive --max-calls")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        packet = load_packet(args.fixture)
        cases = [c for c in packet["cases"] if args.split == "all" or c["split"] == args.split][:args.limit]
        if not cases:
            raise ValueError("no_selected_cases")
        report = {"schema_version": "jev-research-eval-report/1", "mode": args.mode,
                  "fixture_sha256": digest(packet), "provenance": packet["provenance"],
                  "rubric_sha256": {name: hashlib.sha256((Path(rerank.__file__).parent / name).read_bytes()).hexdigest()
                                    for name in ("rerank.py", "jev.py") if (Path(rerank.__file__).parent / name).is_file()},
                  "label_visibility": "Gold labels, expected actions, and split IDs are not provider inputs.",
                  "not_measured": ["retrieval fast versus web", "adaptive versus fixed pivots", "production percentiles", "release gate"],
                  "status": "started", "cases": [], "provider_calls": [], "transport_attempts": [],
                  "planned_judge_calls": sum(bool(c["candidates"]) for c in cases) * sum(a != "deterministic" for a in args.arms)}
        # Reserve before reading configuration or sending any request. Never overwrite a prior receipt.
        with args.output.open("x") as output:
            def save():
                output.seek(0)
                json.dump(report, output, indent=2, ensure_ascii=False, allow_nan=False)
                output.write("\n"); output.truncate(); output.flush()
            save()
            provider = model = jev_client = None
            if args.mode == "live":
                if report["planned_judge_calls"] > args.max_calls:
                    report.update(status="blocked_attempt_budget", remote_attempts=0)
                    save()
                    print(json.dumps({"status": report["status"], "remote_attempts": 0}))
                    return 2
                try:
                    config = env.get_config(policy=env.ConfigLoadPolicy(browser_cookies="off"))
                    if "incumbent" in args.arms:
                        runtime, provider = providers.resolve_runtime(config, "default")
                        model = runtime.rerank_model
                        if provider is None:
                            raise RuntimeError("incumbent_unavailable")
                    if "jev" in args.arms:
                        from lib import jev
                        config["LAST30DAYS_JEV_PROVIDER"] = args.jev_provider
                        jev_client, reason = jev.resolve(config)
                        if jev_client is None:
                            raise RuntimeError("jev_unavailable")
                except Exception as exc:
                    report.update(status="blocked_provider_unavailable", error_type=type(exc).__name__)
                    save()
                    print(json.dumps({"status": report["status"], "remote_attempts": 0}))
                    return 2
            meter = Meter(args.max_calls, time.monotonic() + args.timeout)
            with meter.active():
                for case in cases:
                    for arm in args.arms:
                        first_call, first_attempt = len(meter.calls), len(meter.attempts)
                        row = evaluate_case(case, arm, provider, model, jev_client)
                        row["provider_call_indices"] = list(range(first_call, len(meter.calls)))
                        row["transport_attempt_indices"] = list(range(first_attempt, len(meter.attempts)))
                        if row["status"] == "ok" and row["remote_judge_expected"] and len(meter.attempts) == first_attempt:
                            row.update(status="error", error_type="NoObservedTransportAttempt", ranking=[], metrics=metrics(case, []))
                        report["cases"].append(row)
                        report.update(provider_calls=meter.calls, transport_attempts=meter.attempts)
                        save()
            report.update(status="error" if any(r["status"] == "error" for r in report["cases"]) else "observed",
                          summary=summarize(report["cases"]), remote_attempts=len(meter.attempts))
            costs = [r["cost_usd"] for r in meter.calls]
            report["cost_usd"] = sum(costs) if costs and all(c is not None for c in costs) else (0 if not costs else None)
            report["cost_basis"] = "provider-reported only; null when any call cost is unavailable; not a bill"
            save()
        print(json.dumps({"status": report["status"], "cases": len(report["cases"]), "remote_attempts": len(meter.attempts)}))
        return 1 if report["status"] == "error" else 0
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
