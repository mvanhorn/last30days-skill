"""Response headers and the per-host retry header in the HTTP layer.

A session token can arrive in a response header rather than the body, and a
429 can name its wait in ``x-rate-limit-retry-after-seconds``. Everything here
is mocked at ``urllib.request.urlopen``; no test opens a socket.
"""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from unittest.mock import MagicMock

import pytest

from lib import http


def _headers(pairs: dict[str, str]) -> Message:
    message = Message()
    for name, value in pairs.items():
        message[name] = value
    return message


def _response(body: str, headers: dict[str, str] | None = None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.read.return_value = body.encode("utf-8")
    response.status = 200
    response.headers = _headers(headers or {})
    return response


def _no_network(*_args, **_kwargs):
    pytest.fail("fixture replay attempted the network")


def test_request_that_asks_for_headers_gets_body_and_case_insensitive_headers(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_a, **_k: _response('{"ok": true}', {"X-Request-Id": "abc-123"}),
    )

    body, headers = http.post(
        "https://api.example.test/session", json_data={"q": 1}, return_headers=True
    )

    assert body == {"ok": True}
    assert headers["x-request-id"] == "abc-123"
    assert headers.get("X-REQUEST-ID") == "abc-123"
    assert "X-Request-Id" in headers
    assert headers.get("x-missing") is None
    assert headers.get("x-missing", "fallback") == "fallback"


def test_raw_request_that_asks_for_headers_gets_text_and_headers(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_a, **_k: _response("plain text", {"X-Request-Id": "abc-123"}),
    )

    body, headers = http.request(
        "GET", "https://api.example.test/page", raw=True, return_headers=True
    )

    assert body == "plain text"
    assert headers["X-Request-Id"] == "abc-123"


def test_request_that_does_not_ask_gets_only_the_body(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_a, **_k: _response('{"ok": true}', {"X-Request-Id": "abc-123"}),
    )

    assert http.get("https://api.example.test/search") == {"ok": True}
    assert http.post("https://api.example.test/search", json_data={"q": 1}) == {"ok": True}
    assert http.request("GET", "https://api.example.test/search", raw=True) == '{"ok": true}'


def test_owned_get_refuses_to_promise_headers_it_cannot_read():
    with pytest.raises(ValueError):
        http.request(
            "GET",
            "https://api.example.test/search",
            owned_get=True,
            deadline_monotonic=1e12,
            return_headers=True,
        )


def test_429_with_retry_after_seconds_header_waits_that_long(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    calls = iter([
        urllib.error.HTTPError(
            "https://api.example.test/search",
            429,
            "Too Many Requests",
            _headers({"x-rate-limit-retry-after-seconds": "8"}),
            None,
        ),
        _response('{"ok": true}'),
    ])

    def urlopen(*_args, **_kwargs):
        outcome = next(calls)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)

    assert http.get("https://api.example.test/search") == {"ok": True}
    assert sleeps == [8.0]


def test_retry_delay_reads_the_retry_after_seconds_header():
    assert http.retry_delay_from_headers(
        {"x-rate-limit-retry-after-seconds": "8"}, 3.0
    ) == 8.0
    assert http.retry_delay_from_headers({}, 3.0) == 3.0


def test_recorded_fixture_with_headers_replays_the_same_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_a, **_k: _response('{"ok": true}', {"X-Request-Id": "abc-123"}),
    )
    fixture_dir = tmp_path / "fixture"

    with http.recording_requests(fixture_dir):
        live_body, live_headers = http.post(
            "https://api.example.test/session", json_data={"q": 1}, return_headers=True
        )

    monkeypatch.setattr(http.urllib.request, "urlopen", _no_network)
    with http.replaying_requests(fixture_dir):
        body, headers = http.post(
            "https://api.example.test/session", json_data={"q": 1}, return_headers=True
        )

    assert body == live_body == {"ok": True}
    assert headers["x-request-id"] == live_headers["X-Request-Id"] == "abc-123"
    assert headers.get("X-Request-Id") == "abc-123"


def test_recorded_token_header_is_redacted_on_disk_but_still_present_on_replay(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_a, **_k: _response("{}", {"x-access-token": "dummy-session-SENTINEL"}),
    )
    fixture_dir = tmp_path / "fixture"

    with http.recording_requests(fixture_dir):
        _, live_headers = http.post(
            "https://api.example.test/session", json_data=-1, return_headers=True
        )
    assert live_headers["x-access-token"] == "dummy-session-SENTINEL"

    assert "dummy-session-SENTINEL" not in (fixture_dir / "http.json").read_text(
        encoding="utf-8"
    )

    monkeypatch.setattr(http.urllib.request, "urlopen", _no_network)
    with http.replaying_requests(fixture_dir):
        _, headers = http.post(
            "https://api.example.test/session", json_data=-1, return_headers=True
        )
    assert headers["X-Access-Token"] == "<redacted>"


def test_request_without_headers_records_no_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_a, **_k: _response('{"ok": true}', {"X-Request-Id": "abc-123"}),
    )
    fixture_dir = tmp_path / "fixture"

    with http.recording_requests(fixture_dir):
        http.get("https://api.example.test/search")

    payload = json.loads((fixture_dir / "http.json").read_text(encoding="utf-8"))
    assert payload["exchanges"] == [
        {
            "request": {
                "method": "GET",
                "url": "https://api.example.test/search",
                "raw": False,
            },
            "response": {"value": {"ok": True}},
        }
    ]


def test_fixture_recorded_without_headers_still_replays(tmp_path, monkeypatch):
    fixture = tmp_path / "http.json"
    fixture.write_text(
        json.dumps({
            "format": "last30days-http-fixture/v1",
            "exchanges": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.test/search",
                        "raw": False,
                    },
                    "response": {"value": {"ok": True}},
                },
                {
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.test/search",
                        "raw": False,
                    },
                    "response": {"value": {"ok": True}},
                },
            ],
            "source_exchanges": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(http.urllib.request, "urlopen", _no_network)

    with http.replaying_requests(fixture):
        assert http.get("https://api.example.test/search") == {"ok": True}
        body, headers = http.get("https://api.example.test/search", return_headers=True)

    assert body == {"ok": True}
    assert headers.get("x-access-token") is None


def test_post_sends_a_bare_json_scalar_body(monkeypatch):
    sent = {}

    def urlopen(req, **_kwargs):
        sent["data"] = req.data
        sent["content_type"] = req.get_header("Content-type")
        return _response("{}")

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)

    http.post("https://api.example.test/session", json_data=-1)

    assert sent == {"data": b"-1", "content_type": "application/json"}
