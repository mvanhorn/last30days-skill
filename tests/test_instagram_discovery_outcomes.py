"""Exercise discovery recovery through the real transport and pipeline capture."""
import io
import json
import threading
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import health, http, instagram, pipeline


def response(payload):
    result = io.BytesIO(json.dumps(payload).encode())
    result.status = 200
    return result


def run_discovery(monkeypatch, *, recovery=None, statuses=(401, 429), cancel=None, primary_failure=None,
                  primary_failure_body=None):
    reel = {"id": "recovered", "shortcode": "RECOVERED", "taken_at": "2026-08-16T12:00:00Z",
            "video_play_count": 10, "owner": {"username": "creator"}}
    queries = ["first", "second"] if recovery != "creator" else ["first"]

    def transport(request, *args, **kwargs):
        url = urlsplit(request.full_url)
        query = parse_qs(url.query)
        if url.path.endswith("/user/reels"):
            return response({"reels": [reel]})
        if url.path.endswith("/reels/search"):
            if primary_failure and query["query"][0] == "first":
                body = primary_failure_body if primary_failure_body is not None else {}
                raise urllib.error.HTTPError(
                    request.full_url, primary_failure, "Error", {}, io.BytesIO(json.dumps(body).encode())
                )
            recovered = query["query"][0] == recovery
            return response({"reels": [reel] if recovered else []})
        assert url.path.endswith("/google/search")
        status = statuses[0] if "first" in query["query"][0] else statuses[1]
        if cancel is not None:
            cancel.set()
        if status is None:
            raise urllib.error.URLError("connection refused")
        raise urllib.error.HTTPError(request.full_url, status, "Error", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(http.urllib.request, "urlopen", transport)
    monkeypatch.setattr(http.time, "sleep", lambda _: None)
    monkeypatch.setattr(instagram, "expand_instagram_queries", lambda *_: queries)
    monkeypatch.setattr(instagram, "fetch_captions", lambda *args, **kwargs: {})
    returned = {}

    def retrieve(**kwargs):
        result = instagram.search_and_enrich(
            "topic", "2026-08-01", "2026-08-31", depth="default", token="dummy-token",
            ig_creators=["creator"] if recovery == "creator" else None, cancel=cancel,
        )
        returned.update(result)
        return result["items"], pipeline._result_outcome_artifact("instagram", result)

    monkeypatch.setattr(pipeline, "_retrieve_stream_impl", retrieve)
    items, artifact = pipeline._retrieve_stream(source="instagram")
    return returned, items, artifact


@pytest.mark.parametrize("recovery", ["first", "second", "creator"])
def test_discovery_failure_is_detail_when_another_query_or_creator_recovers(monkeypatch, recovery):
    result, items, artifact = run_discovery(monkeypatch, recovery=recovery)
    assert len(items) == 1
    assert not result.get("error")
    assert "_source_outcome" not in artifact
    assert "1 sub-request" in artifact["_source_outcome_detail"]


@pytest.mark.parametrize("statuses", [(401, 429), (429, 401)])
def test_empty_multi_query_result_keeps_auth_failure_ahead_of_rate_limit(monkeypatch, statuses):
    result, items, artifact = run_discovery(monkeypatch, statuses=statuses)
    assert items == []
    assert "401" in result["error"]
    assert artifact["_source_outcome"]["state"] == health.AUTH_FAILED
    assert "Google Instagram discovery fallback" in artifact["_source_outcome"]["detail"]


def test_discovery_recovery_does_not_clear_research_cancellation(monkeypatch):
    result, items, artifact = run_discovery(monkeypatch, recovery="creator", cancel=threading.Event())
    assert len(items) == 1
    assert result["error"] == instagram.RESEARCH_TIMEOUT
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


@pytest.mark.parametrize("statuses", [(408, None), (None, 408)])
def test_empty_multi_query_keeps_typed_timeout_ahead_of_network_error(monkeypatch, statuses):
    result, items, artifact = run_discovery(monkeypatch, statuses=statuses)
    assert items == []
    assert "408" in result["error"]
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


def test_keyword_recovery_does_not_clear_a_primary_search_auth_failure(monkeypatch):
    result, items, artifact = run_discovery(monkeypatch, recovery="second", primary_failure=401)
    assert len(items) == 1
    assert "401" in result["error"]
    assert artifact["_source_outcome"]["state"] == health.AUTH_FAILED


@pytest.mark.parametrize("primary_status", [408, 404])
def test_primary_or_composite_timeout_is_not_masked_by_later_discovery_network_failure(monkeypatch, primary_status):
    result, items, artifact = run_discovery(
        monkeypatch, primary_failure=primary_status, statuses=(408, None),
    )
    assert items == []
    assert "408" in result["error"]
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


def test_documented_primary_404_and_fallback_404_is_clean_no_results(monkeypatch):
    result, items, artifact = run_discovery(
        monkeypatch,
        primary_failure=404,
        primary_failure_body={"error": "not_found", "message": "No reels found for that query"},
        statuses=(404, 404),
    )
    assert items == []
    assert not result.get("error")
    assert "_source_outcome" not in artifact
