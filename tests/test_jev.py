"""Offline System One wire and rejection contracts; all credentials are dummy."""
import time
from unittest.mock import Mock

import pytest
from lib import jev


def question():
    return {"relevance": {"type": "score", "instructions": "Rate relevance", "criteria": ["No", "Yes"]},
            "entity": {"type": "noul", "instructions": "Same entity?"}}


def response():
    return {"model": "typesafe/jev-1.13-20260917", "usage": {"input_tokens": 10, "output_tokens": 4, "cost": 0.0001},
            "answers": {"relevance": {"type": "score", "score": 0.8, "confidence": 0.8,
                         "probabilities": {"0": 0.2, "1": 0.8}, "legend": {"0": "No", "1": "Yes"}},
                        "entity": {"type": "noul", "noul": 0.9}}}


# Independent literal expected IDs from the two providers' official documentation.
@pytest.mark.parametrize("route,url,wire_model", [("typesafe", "https://api.typesafe.ai/v1/systemone", "jev-1.13.0"),
                                       ("openrouter", "https://openrouter.ai/api/v1/systemone", "typesafe/jev-1.13")])
def test_wire_and_receipt(monkeypatch, route, url, wire_model):
    post = Mock(return_value=response())
    monkeypatch.setattr(jev.http, "post", post)
    c = jev.JevClient(route, "dummy-key")
    result = c.evaluate("public evidence", question())
    args, kw = post.call_args
    assert args[0] == url and args[1]["model"] == wire_model
    assert c.model == wire_model and c.last_receipt["requested_model"] == wire_model
    assert kw["headers"] == {"Authorization": "Bearer dummy-key"}
    assert kw["retries"] == 1 and kw["retry_dns"] is False
    assert 0 < kw["timeout"] <= 10 and kw["deadline_monotonic"] > time.monotonic()
    assert result == response()["answers"]
    assert c.last_receipt["usage"]["cost"] == 0.0001 and c.last_receipt["status"] == "ok"
    assert post.call_count == 1 and "dummy-key" not in repr(c.last_receipt)


def test_resolver_uses_resolved_keys_and_respects_route_override(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ambient-ignored")
    assert jev.resolve({}) == (None, "missing_key")
    assert jev.resolve({"TYPESAFE_API_KEY": "dummy"})[0].provider == "typesafe"
    assert jev.resolve({"OPENROUTER_API_KEY": "dummy"})[0].provider == "openrouter"
    assert jev.resolve({"LAST30DAYS_JEV_PROVIDER": "off", "TYPESAFE_API_KEY": "dummy"}) == (None, "off")
    assert jev.resolve({"LAST30DAYS_JEV_PROVIDER": "auto"}) == (None, "missing_key")
    config = {"LAST30DAYS_JEV_PROVIDER": "auto", "TYPESAFE_API_KEY": "dummy", "OPENROUTER_API_KEY": "dummy-or"}
    assert jev.resolve(config)[0].provider == "typesafe"
    del config["TYPESAFE_API_KEY"]
    assert jev.resolve(config)[0].provider == "openrouter"
    config["LAST30DAYS_JEV_PROVIDER"] = "typesafe"
    assert jev.resolve(config) == (None, "missing_key")
    assert jev.resolve({"LAST30DAYS_JEV_PROVIDER": "bad"}) == (None, "invalid_provider")


@pytest.mark.parametrize("bad", [None, {}, {"other": {"type": "noul", "noul": 1}},
                                  {**response()["answers"], "extra": {}}])
def test_full_answer_ids(monkeypatch, bad):
    body = response(); body["answers"] = bad
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    with pytest.raises(jev.JevError):
        jev.JevClient("typesafe", "dummy").evaluate("public", question())


@pytest.mark.parametrize("field,value", [("noul", float("nan")), ("noul", float("inf")), ("noul", True),
                                        ("noul", -0.1), ("noul", 1.1), ("type", "score")])
def test_invalid_noul(monkeypatch, field, value):
    body = response(); body["answers"]["entity"][field] = value
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    with pytest.raises(jev.JevError):
        jev.JevClient("typesafe", "dummy").evaluate("public", question())


@pytest.mark.parametrize("patch", [{"score": 0.1}, {"score": float("nan")}, {"confidence": True},
                                   {"probabilities": {"0": 0.1, "1": 0.1}},
                                   {"probabilities": {"0": -0.1, "1": 1.1}},
                                   {"legend": {"0": "different", "1": "Yes"}}])
def test_invalid_score(monkeypatch, patch):
    body = response(); body["answers"]["relevance"].update(patch)
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    with pytest.raises(jev.JevError):
        jev.JevClient("typesafe", "dummy").evaluate("public", question())


def test_rounded_probabilities_and_score_accept_valid_provider_precision(monkeypatch):
    questions = {"relevance": {"type": "score", "instructions": "Rate relevance",
                               "criteria": ["No", "Partial", "Yes"]}}
    body = response()
    body["answers"] = {"relevance": {
        "type": "score", "score": 1.0, "confidence": 0.5,
        "probabilities": {"0": 0.33, "1": 0.33, "2": 0.33},
        "legend": {"0": "No", "1": "Partial", "2": "Yes"},
    }}
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    client = jev.JevClient("typesafe", "dummy")
    assert client.evaluate("public", questions)["relevance"]["score"] == 1.0

    body["answers"]["relevance"]["score"] = 1.1
    with pytest.raises(jev.JevError, match="Score is inconsistent"):
        client.evaluate("public", questions)
    body["answers"]["relevance"]["score"] = 1.0
    body["answers"]["relevance"]["probabilities"] = {"0": 0.3, "1": 0.3, "2": 0.3}
    with pytest.raises(jev.JevError, match="Probabilities do not sum"):
        client.evaluate("public", questions)


@pytest.mark.parametrize("patch", [{"model": "other-model"}, {"usage": {"cost": float("nan")}},
                                   {"usage": {"input_tokens": True}}, {"usage": None}])
def test_invalid_metadata(monkeypatch, patch):
    body = response(); body.update(patch)
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    with pytest.raises(jev.JevError):
        jev.JevClient("typesafe", "dummy").evaluate("public", question())


def test_limits_and_deadline_make_no_call(monkeypatch):
    post = Mock(); monkeypatch.setattr(jev.http, "post", post)
    c = jev.JevClient("typesafe", "dummy")
    for state, questions in [("public", {}), ("public", {str(i): question()["entity"] for i in range(33)}),
                              ("é" * 12000, question())]:
        with pytest.raises(jev.JevError):
            c.evaluate(state, questions)
    c.deadline_monotonic = time.monotonic() - 1
    with pytest.raises(jev.JevError, match="deadline_exceeded"):
        c.evaluate("public", question())
    post.assert_not_called()


def test_error_is_safe_and_no_second_provider(monkeypatch):
    post = Mock(side_effect=jev.http.HTTPError("secret-key and private response"))
    monkeypatch.setattr(jev.http, "post", post)
    c = jev.JevClient("typesafe", "dummy")
    with pytest.raises(jev.JevError, match="^request_failed$"):
        c.evaluate("public", question())
    assert post.call_count == 1 and c.last_receipt["status"] == "failed"
    assert "secret" not in repr(c.last_receipt)


def test_choice_validation():
    q = {"type": "choice", "instructions": "Choose", "criteria": {"a": "A", "b": "B"}}
    a = {"type": "choice", "choice": "a", "confidence": 0.9, "probabilities": {"a": 0.9, "b": 0.1}}
    assert jev.validate_answer(a, q) == a
    with pytest.raises(jev.JevError):
        jev.validate_answer({**a, "choice": "b"}, q)


def test_explicit_timeout_and_deadline_are_shared(monkeypatch):
    post = Mock(return_value=response()); monkeypatch.setattr(jev.http, "post", post)
    c = jev.JevClient("typesafe", "dummy", timeout_seconds=20)
    c.deadline_monotonic = time.monotonic() + 0.5
    c.evaluate("public", question(), timeout=2)
    kw = post.call_args.kwargs
    assert 0 < kw["timeout"] <= 0.5 and kw["deadline_monotonic"] == c.deadline_monotonic


def test_absent_cost_is_unknown_not_zero(monkeypatch):
    body = response(); del body["usage"]["cost"]
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    c = jev.JevClient("openrouter", "dummy")
    c.evaluate("public", question())
    assert "cost" not in c.last_receipt["usage"]


def test_requests_have_no_separate_call_quota(monkeypatch):
    post = Mock(return_value=response()); monkeypatch.setattr(jev.http, "post", post)
    client = jev.JevClient("typesafe", "dummy")
    for _ in range(13):
        client.evaluate("public", question())
    assert post.call_count == 13 and client.last_receipt["status"] == "ok"


def test_shared_http_has_one_dns_attempt(monkeypatch):
    import socket
    import urllib.error
    opener = Mock(side_effect=urllib.error.URLError(socket.gaierror(-2, "fixture DNS failure")))
    monkeypatch.setattr(jev.http, "_open_request", opener)
    c = jev.JevClient("typesafe", "dummy")
    with pytest.raises(jev.JevError, match="request_failed"):
        c.evaluate("public", question())
    assert opener.call_count == 1


@pytest.mark.parametrize("model", ["jev-1.13.0", "jev-1.13.1", "jev-1.13", "typesafe/jev-1.13-20260917"])
def test_documented_pinned_model_snapshots(monkeypatch, model):
    body = response(); body["model"] = model
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    c = jev.JevClient("typesafe", "dummy")
    c.evaluate("public", question())
    assert c.last_receipt["model"] == model


@pytest.mark.parametrize("model", ["jev-latest", "jev-1.14.0", "untrusted/jev-1.13.0", "jev-1.13.0 extra"])
def test_wrong_model_family_is_rejected(monkeypatch, model):
    body = response(); body["model"] = model
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    with pytest.raises(jev.JevError, match="invalid_model"):
        jev.JevClient("typesafe", "dummy").evaluate("public", question())


def test_http_failure_receipt_keeps_only_safe_status(monkeypatch):
    error = jev.http.HTTPError("secret message", status_code=429, body="secret body", outcome_state="rate-limited")
    monkeypatch.setattr(jev.http, "post", Mock(side_effect=error))
    c = jev.JevClient("typesafe", "dummy")
    with pytest.raises(jev.JevError, match="request_failed"):
        c.evaluate("public", question())
    assert c.last_receipt["status_code"] == 429
    assert c.last_receipt["outcome_state"] == "rate-limited"
    assert "secret" not in repr(c.last_receipt)


def test_http_failure_receipt_rejects_untrusted_status_values(monkeypatch):
    error = jev.http.HTTPError("secret message", status_code="secret status", outcome_state="secret outcome")
    monkeypatch.setattr(jev.http, "post", Mock(side_effect=error))
    c = jev.JevClient("typesafe", "dummy")
    with pytest.raises(jev.JevError):
        c.evaluate("public", question())
    assert "status_code" not in c.last_receipt and "outcome_state" not in c.last_receipt
    assert "secret" not in repr(c.last_receipt)


@pytest.mark.parametrize("field", ["noul", "score", "confidence", "probability", "cost"])
def test_oversized_json_integer_is_a_safe_validation_failure(monkeypatch, field):
    body = response()
    huge = 10 ** 1000
    if field == "noul":
        body["answers"]["entity"]["noul"] = huge
    elif field == "probability":
        body["answers"]["relevance"]["probabilities"]["0"] = huge
    elif field == "cost":
        body["usage"]["cost"] = huge
    else:
        body["answers"]["relevance"][field] = huge
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    client = jev.JevClient("typesafe", "dummy")
    with pytest.raises(jev.JevError):
        client.evaluate("public", question())
    assert client.last_receipt["status"] == "failed"


@pytest.mark.parametrize("where", ["reason", "body", "truncated_body", "url_error", "socket_error"])
def test_shared_transport_debug_redacts_echoed_key(monkeypatch, capsys, where):
    import io
    import urllib.error

    key = "DUMMY_KEY_ECHO_731_abcdefghijklmnopqrstuvwxyz"
    reason = key if where == "reason" else "Unauthorized"
    body = ("x" * 190 if where == "truncated_body" else "Invalid token ") + key
    error = urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", 401, reason, {}, io.BytesIO(body.encode()))
    if where == "url_error":
        error = urllib.error.URLError("Connection failed for " + key)
    elif where == "socket_error":
        error = OSError("Connection failed for " + key)
    opener = Mock(side_effect=error)
    monkeypatch.setattr(jev.http, "_open_request", opener)
    monkeypatch.setenv("LAST30DAYS_DEBUG", "1")
    client = jev.JevClient("typesafe", key)
    with pytest.raises(jev.JevError, match="^request_failed$"):
        client.evaluate("public", question())
    captured = capsys.readouterr()
    assert "DUMMY_KEY_ECHO" not in captured.err
    assert "<redacted>" in captured.err
    assert key not in repr(client.last_receipt)
    assert opener.call_count == 1
    if where not in {"url_error", "socket_error"}:
        assert client.last_receipt["status_code"] == 401


def test_invalid_answer_retains_valid_observed_usage(monkeypatch):
    body = response()
    body["answers"] = {}
    monkeypatch.setattr(jev.http, "post", Mock(return_value=body))
    client = jev.JevClient("typesafe", "dummy")
    with pytest.raises(jev.JevError, match="answer_ids_mismatch"):
        client.evaluate("public", question())
    assert client.last_receipt["usage"] == body["usage"]
    assert client.last_receipt["model"] == body["model"]
    assert client.last_receipt["status"] == "failed"


@pytest.mark.parametrize("mode,keys,expected", [
    ("off", {"TYPESAFE_API_KEY": "dummy"}, (None, "off")),
    ("auto", {}, (None, "missing_key")),
    ("auto", {"TYPESAFE_API_KEY": " ", "OPENROUTER_API_KEY": "\t"}, (None, "missing_key")),
    ("typesafe", {"OPENROUTER_API_KEY": "dummy"}, (None, "missing_key")),
    ("openrouter", {"TYPESAFE_API_KEY": "dummy"}, (None, "missing_key")),
    ("auto", {"TYPESAFE_API_KEY": "dummy", "OPENROUTER_API_KEY": "dummy"}, ("typesafe", "ready")),
    ("auto", {"OPENROUTER_API_KEY": "dummy"}, ("openrouter", "ready")),
])
def test_configured_route_never_constructs_client(monkeypatch, mode, keys, expected):
    factory = Mock(side_effect=AssertionError("client factory"))
    monkeypatch.setattr(jev, "JevClient", factory)
    config = {"LAST30DAYS_JEV_PROVIDER": mode, **keys}
    assert jev.configured_route(config) == expected
    if expected[0] is None:
        assert jev.resolve(config) == expected
    factory.assert_not_called()
