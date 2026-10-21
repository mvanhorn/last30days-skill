import io
import json
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import health, http, instagram, pipeline, schema


def response(payload):
    result = io.BytesIO(json.dumps(payload).encode())
    result.status = 200
    return result


def post(shortcode):
    return {"data": {"xdt_shortcode_media": {
        "id": shortcode,
        "shortcode": shortcode,
        "taken_at_timestamp": 1786900800,
        "caption": "Coffee roasting guide",
        "owner": {"username": "test_creator"},
    }}}


def test_stream_retains_completed_detail_before_its_deadline(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    requests = []

    def transport(request, timeout):
        url = urlsplit(request.full_url)
        query = parse_qs(url.query)
        requests.append((url.path, timeout))
        if url.path == "/v2/instagram/reels/search":
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        if url.path == "/v1/google/search":
            return response({"results": [
                {"url": "https://www.instagram.com/reel/SLOW/"},
                {"url": "https://www.instagram.com/reel/FAST/"},
            ]})
        if url.path == "/v1/instagram/post":
            if "/SLOW/" in query["url"][0]:
                try:
                    release.wait(3)
                    return response(post("SLOW"))
                finally:
                    finished.set()
            return response(post("FAST"))
        raise AssertionError(f"Unexpected paid request: {url.path}")

    monkeypatch.setattr(http, "_open_request", transport)
    config = {
        "SCRAPECREATORS_API_KEY": "dummy-key",
        "INCLUDE_SOURCES": "instagram,instagram_comments",
        "_research_deadline": time.monotonic() + 1,
    }
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            pipeline._retrieve_stream,
            topic="coffee",
            subquery=schema.SubQuery(
                label="primary", search_query="coffee", ranking_query="coffee",
                sources=["instagram"], weight=1.0,
            ),
            source="instagram", config=config, depth="default",
            date_range=("2026-08-01", "2026-08-31"), runtime=Mock(), mock=False,
        )
        try:
            done, pending = pipeline._wait_for_research([future], config)
            assert not pending, "Instagram must return its recovered reels before the stream expires"
            items, artifact = future.result()
            assert [item["video_id"] for item in items] == ["FAST"]
            assert artifact["_source_outcome"]["state"] == health.PARTIAL
            assert "timed out" in artifact["_source_outcome"]["detail"]
            assert len(requests) == 4
            assert all(0 < timeout < 1 for _, timeout in requests)
        finally:
            release.set()
            assert finished.wait(2)


@pytest.mark.parametrize("stage", ["creator", "query", "transcript"])
def test_cancellation_retains_items_and_stops_later_requests(monkeypatch, stage):
    cancel = threading.Event()
    requests = []
    reel = {
        "id": "EARLY", "shortcode": "EARLY", "caption": "Coffee roasting guide",
        "taken_at": "2026-08-16T12:00:00Z", "owner": {"username": "test_creator"},
    }

    def transport(request, timeout):
        path = urlsplit(request.full_url).path
        requests.append(path)
        if path == "/v1/instagram/user/reels":
            if requests.count(path) == 2:
                cancel.set()
            return response({"items": [{"media": reel}]})
        if path == "/v2/instagram/reels/search":
            if stage == "query" and requests.count(path) == 2:
                cancel.set()
            return response({"reels": [reel]})
        if path == "/v2/instagram/media/transcript":
            cancel.set()
            return response({"transcripts": [{"text": "Fresh transcript"}]})
        raise AssertionError(f"Unexpected paid request: {path}")

    monkeypatch.setattr(http, "_open_request", transport)
    result = instagram.search_and_enrich(
        "coffee", "2026-08-01", "2026-08-31", token="dummy-key",
        depth="quick" if stage == "transcript" else "deep",
        ig_creators=["first", "second", "third"] if stage == "creator" else None,
        deadline=time.monotonic() + 2, cancel=cancel,
    )
    assert [item["video_id"] for item in result["items"]] == ["EARLY"]
    expected = {
        "creator": ["/v1/instagram/user/reels"] * 2,
        "query": ["/v2/instagram/reels/search"] * 2,
        "transcript": ["/v2/instagram/reels/search", "/v2/instagram/media/transcript"],
    }
    assert requests == expected[stage]
    assert "cancelled" in result["error"]


def test_transcript_timeout_preserves_free_captions_and_skips_next_transcript(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    requests = []
    items = [
        {"video_id": name, "url": f"https://www.instagram.com/reel/{name}/", "text": f"Caption {name}"}
        for name in ("FIRST", "SECOND")
    ]

    def transport(request, timeout):
        requests.append(request.full_url)
        try:
            release.wait(2)
            return response({"transcripts": [{"text": "Too late"}]})
        finally:
            finished.set()

    monkeypatch.setattr(http, "_open_request", transport)
    try:
        captions = instagram.fetch_captions(items, "dummy-key", deadline=time.monotonic() + 0.15)
        assert captions == {"FIRST": "Caption FIRST", "SECOND": "Caption SECOND"}
        assert len(requests) == 1
    finally:
        release.set()
        assert finished.wait(2)


def test_comment_timeout_keeps_completed_comments_without_late_mutation(monkeypatch):
    release = threading.Event()
    finished = threading.Event()
    items = [
        {"video_id": name, "url": f"https://www.instagram.com/reel/{name}/", "engagement": {"likes": 10}}
        for name in ("SLOW", "FAST")
    ]

    def transport(request, timeout):
        query = parse_qs(urlsplit(request.full_url).query)
        if "/SLOW/" in query["url"][0]:
            try:
                release.wait(2)
                return response({"comments": [{"text": "Too late", "comment_like_count": 100}]})
            finally:
                finished.set()
        return response({"comments": [{"text": "Helpful early comment", "comment_like_count": 50}]})

    monkeypatch.setattr(http, "_open_request", transport)
    try:
        result = instagram.enrich_with_comments(items, "dummy-key", deadline=time.monotonic() + 0.15)
        assert result is items
        assert "top_comments" not in items[0]
        assert items[1]["top_comments"][0]["text"] == "Helpful early comment"
        snapshot = json.dumps(items)
    finally:
        release.set()
        assert finished.wait(2)
    assert json.dumps(items) == snapshot


def test_cancellation_stops_queued_detail_requests(monkeypatch):
    cancel = threading.Event()
    in_flight = threading.Barrier(4)
    requests = []

    def transport(request, timeout):
        path = urlsplit(request.full_url).path
        requests.append(path)
        if path == "/v1/google/search":
            return response({"results": [
                {"url": f"https://www.instagram.com/reel/POST{index}/"}
                for index in range(9)
            ]})
        in_flight.wait(timeout=2)
        cancel.set()
        return response(post("POST0"))

    monkeypatch.setattr(http, "_open_request", transport)
    instagram._google_reel_fallback(
        "coffee", "quick", "dummy-key", "2026-08-01", "2026-08-31",
        deadline=time.monotonic() + 2, cancel=cancel,
    )
    assert requests == ["/v1/google/search"] + ["/v1/instagram/post"] * 4


@pytest.mark.parametrize("depth,pages,posts", [("quick", 1, 9), ("default", 2, 16), ("deep", 3, 27)])
def test_deadline_enabled_recovery_preserves_paid_request_caps(monkeypatch, depth, pages, posts):
    requests = []
    page_count = 0

    def transport(request, timeout):
        nonlocal page_count
        url = urlsplit(request.full_url)
        query = parse_qs(url.query)
        requests.append(url.path)
        if url.path == "/v2/instagram/reels/search":
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        if url.path == "/v1/google/search":
            page_count += 1
            return response({"results": [
                {"url": f"https://www.instagram.com/reel/P{page_count}_{index}/"}
                for index in range(40)
            ]})
        if url.path == "/v1/instagram/post":
            return response(post(urlsplit(query["url"][0]).path.split("/")[2]))
        if url.path == "/v2/instagram/media/transcript":
            return response({"transcripts": []})
        raise AssertionError(f"Unexpected paid request: {url.path}")

    monkeypatch.setattr(http, "_open_request", transport)
    result = instagram.search_and_enrich(
        "coffee", "2026-08-01", "2026-08-31", token="dummy-key", depth=depth,
        deadline=time.monotonic() + 5,
    )
    assert len(result["items"]) == posts
    assert not result["error"]
    assert requests.count("/v1/google/search") == pages
    assert requests.count("/v1/instagram/post") == posts
