"""Key-driven System One judgments. No ambient key lookup or provider failover."""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Mapping

from . import http

# Direct model ID: https://docs.typesafe.ai/models
# OpenRouter version route: https://openrouter.ai/docs/guides/community/typesafe-sdk
# OpenRouter resolves this version route to a snapshot, retained in each receipt.
MODELS = {"typesafe": "jev-1.13.0", "openrouter": "typesafe/jev-1.13"}
MODEL = MODELS["typesafe"]  # Compatibility only; wire callers use client.model.
ENDPOINTS = {
    "typesafe": "https://api.typesafe.ai/v1/systemone",
    "openrouter": "https://openrouter.ai/api/v1/systemone",
}
MAX_QUESTIONS = 32
MAX_REQUEST_BYTES = 64_000


class JevError(ValueError):
    """Safe failure code; never includes remote response text or credentials."""


def finite_number(value: Any, minimum: float, maximum: float) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def validate_question(question: Mapping[str, Any]) -> None:
    if not isinstance(question.get("instructions"), str) or not question["instructions"].strip():
        raise JevError("Question instructions must be nonempty text")
    kind, criteria = question.get("type"), question.get("criteria")
    if kind == "choice":
        if not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255:
            raise JevError("Choice requires 1 to 255 criteria")
        if any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in criteria.items()):
            raise JevError("Choice criteria must map nonempty IDs to text")
    elif kind == "score":
        if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
            raise JevError("Score requires 2 to 10 ordered criteria")
        if any(not isinstance(v, str) for v in criteria):
            raise JevError("Score criteria must be text")
    elif kind == "noul":
        if criteria is not None and (
            not isinstance(criteria, dict) or set(criteria) != {"true", "false"}
            or any(not isinstance(v, str) for v in criteria.values())
        ):
            raise JevError("Noul criteria must describe true and false")
    else:
        raise JevError("Unsupported question type")
    if set(question) - {"type", "instructions", "criteria"}:
        raise JevError("Unsupported question fields")


def validate_distribution(value: Any, keys: set[str]) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != keys:
        raise JevError("Probability keys do not match the requested criteria")
    if any(not finite_number(v, 0, 1) for v in value.values()):
        raise JevError("Invalid probability value")
    # Native probabilities and scores are rounded independently to two decimals.
    if not any(value.values()):
        raise JevError("Probability distribution has no mass")
    if not math.isclose(sum(value.values()), 1.0, abs_tol=0.005 * len(keys) + 1e-9):
        raise JevError("Probabilities do not sum to one")
    return dict(value)


def validate_answer(answer: Any, question: Mapping[str, Any]) -> dict[str, Any]:
    kind = question["type"]
    if not isinstance(answer, dict) or answer.get("type") != kind:
        raise JevError("Answer type differs from the requested question")
    if kind == "noul":
        if not finite_number(answer.get("noul"), 0, 1):
            raise JevError("Invalid Noul answer")
        return {"type": kind, "noul": answer["noul"]}
    keys = set(question["criteria"]) if kind == "choice" else {str(i) for i in range(len(question["criteria"]))}
    probabilities = validate_distribution(answer.get("probabilities"), keys)
    if not finite_number(answer.get("confidence"), 0, 1):
        raise JevError("Invalid native confidence")
    result: dict[str, Any] = {"type": kind, "probabilities": probabilities, "confidence": answer["confidence"]}
    if kind == "choice":
        choice = answer.get("choice")
        if not isinstance(choice, str) or choice not in keys:
            raise JevError("Answer chose an unknown option")
        if probabilities[choice] < max(probabilities.values()) - 0.000001:
            raise JevError("Choice is inconsistent with its distribution")
        result["choice"] = choice
    else:
        legend = {str(i): v for i, v in enumerate(question["criteria"])}
        if answer.get("legend") != legend or not finite_number(answer.get("score"), 0, len(keys) - 1):
            raise JevError("Invalid score or legend")
        expected = sum(int(k) * v for k, v in probabilities.items())
        if not math.isclose(answer["score"], expected, abs_tol=0.005 * (1 + sum(int(k) for k in keys)) + 1e-9):
            raise JevError("Score is inconsistent with its distribution")
        result.update(score=answer["score"], legend=legend)
    return result


def _build_payload(
    model: str, state: str, questions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate the request before any transport attempt."""
    if not isinstance(state, str) or not isinstance(questions, Mapping) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise JevError("invalid_packet")
    for key, question in questions.items():
        if not isinstance(key, str) or not key or not isinstance(question, Mapping):
            raise JevError("invalid_question")
        validate_question(question)
    payload = {"model": model, "state": state, "questions": dict(questions)}
    if len(json.dumps(payload, allow_nan=False).encode("utf-8")) > MAX_REQUEST_BYTES:
        raise JevError("packet_too_large")
    return payload


def _validate_usage(usage: Any) -> dict[str, int | float]:
    if not isinstance(usage, dict):
        raise JevError("invalid_usage")
    clean_usage: dict[str, int | float] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "cost"):
        if key not in usage:
            continue
        value = usage[key]
        if key == "cost":
            valid = finite_number(value, 0, float("inf"))
        else:
            valid = type(value) is int and value >= 0
        if not valid:
            raise JevError("invalid_usage")
        clean_usage[key] = value
    return clean_usage


def _validate_response(
    response: Any, questions: Mapping[str, Mapping[str, Any]], receipt: dict[str, Any],
) -> dict[str, Any]:
    """Keep validated metadata even when the answer packet is rejected."""
    if not isinstance(response, dict):
        raise JevError("invalid_response")
    model = response.get("model")
    if not isinstance(model, str) or not re.fullmatch(r"(?:typesafe/)?jev-1\.13(?:\.\d+)?(?:-\d{8})?", model):
        raise JevError("invalid_model")
    receipt["model"] = model
    receipt["usage"] = _validate_usage(response.get("usage"))
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevError("answer_ids_mismatch")
    return {key: validate_answer(answers[key], q) for key, q in questions.items()}


def configured_route(config: Mapping[str, Any]) -> tuple[str | None, str]:
    """Check resolved credentials without creating a client or reading ambient keys."""
    mode = str(config.get("LAST30DAYS_JEV_PROVIDER") or "auto").strip().lower()
    if mode == "off":
        return None, "off"
    if mode not in {"auto", *ENDPOINTS}:
        return None, "invalid_provider"
    routes = ("typesafe", "openrouter") if mode == "auto" else (mode,)
    for route in routes:
        key = config.get("TYPESAFE_API_KEY" if route == "typesafe" else "OPENROUTER_API_KEY")
        if isinstance(key, str) and key.strip():
            return route, "ready"
    return None, "missing_key"


def resolve(config: Mapping[str, Any]) -> tuple[JevClient | None, str]:
    route, reason = configured_route(config)
    if route is None:
        return None, reason
    key = config["TYPESAFE_API_KEY" if route == "typesafe" else "OPENROUTER_API_KEY"]
    return JevClient(route, key.strip()), "ready"


class JevClient:
    def __init__(self, provider: str, api_key: str, *, timeout_seconds: float = 10.0) -> None:
        if provider not in ENDPOINTS or not isinstance(api_key, str) or not api_key.strip():
            raise JevError("invalid_configuration")
        if not finite_number(timeout_seconds, 0.001, 30):
            raise JevError("invalid_timeout")
        self.provider = provider
        self.model = MODELS[provider]
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.deadline_monotonic: float | None = None
        self.last_receipt: dict[str, Any] = {}

    def evaluate(self, state: str, questions: Mapping[str, Mapping[str, Any]], *, timeout: float | None = None) -> dict[str, Any]:
        """Validate a complete response or reject it; never retry another route.

        Callers supply public evidence only. Packet size is measured using the
        same JSON encoding as the shared HTTP client. Returned usage is observed,
        never estimated; missing cost is unknown, not zero.
        """
        started = time.monotonic()
        self.last_receipt = {"provider": self.provider, "requested_model": self.model,
                             "model": None, "usage": {}, "status": "rejected", "latency_ms": 0.0}
        try:
            payload = _build_payload(self.model, state, questions)
            seconds = self.timeout_seconds if timeout is None else min(self.timeout_seconds, timeout)
            if not finite_number(seconds, 0.001, 30):
                raise JevError("invalid_timeout")
            deadline = started + seconds
            if self.deadline_monotonic is not None:
                deadline = min(deadline, self.deadline_monotonic)
            seconds = min(seconds, deadline - time.monotonic())
            if seconds <= 0:
                raise JevError("deadline_exceeded")
            self.last_receipt["status"] = "failed"
            response = http.post(ENDPOINTS[self.provider], payload,
                                 headers={"Authorization": f"Bearer {self._api_key}"},
                                 timeout=seconds, retries=1, max_429_retries=1,
                                 retry_dns=False, deadline_monotonic=deadline)
            normalized = _validate_response(response, questions, self.last_receipt)
            self.last_receipt["status"] = "ok"
            return normalized
        except JevError:
            raise
        except http.HTTPError as exc:
            if type(exc.status_code) is int and 100 <= exc.status_code <= 599:
                self.last_receipt["status_code"] = exc.status_code
            if isinstance(exc.outcome_state, str) and exc.outcome_state in {"rate-limited", "auth-failed", "payment-required", "unreachable",
                                     "timeout", "schema-drift", "error"}:
                self.last_receipt["outcome_state"] = exc.outcome_state
            raise JevError("request_failed") from None
        except (OSError, ValueError, TypeError, KeyError):
            raise JevError("request_failed") from None
        finally:
            self.last_receipt["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
