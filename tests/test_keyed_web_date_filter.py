"""Keyed web results with unknown dates keep their date uncertainty (#928).

Brave, Exa, and Serper bound both ends at the provider. Parallel bounds the
start date and retains undated results only for current-window searches.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lib import grounding, normalize, render

DATE_RANGE = ("2026-02-25", "2026-03-27")
TODAY = datetime.now(timezone.utc).date()
LIVE_DATE_RANGE = ((TODAY - timedelta(days=30)).isoformat(), TODAY.isoformat())


@pytest.mark.parametrize(
    ("search", "payload"),
    [
        (grounding.brave_search, {"web": {"results": [
            {"title": "Undated", "url": "https://example.com/brave"},
        ]}}),
        (grounding.exa_search, {"results": [
            {"title": "Undated", "url": "https://example.com/exa"},
        ]}),
        (grounding.serper_search, {"organic": [
            {"title": "Undated", "link": "https://example.com/serper"},
        ]}),
    ],
)
def test_server_bounded_undated_result_survives_normalization(search, payload):
    with patch("lib.grounding.http.request", return_value=payload):
        raw, _ = search("test", DATE_RANGE, "fake-key")
    normalized = normalize.normalize_source_items("grounding", raw, *DATE_RANGE)
    assert len(normalized) == 1
    assert normalized[0].published_at is None
    assert normalized[0].date_confidence == "low"
    assert render._format_date(normalized[0]) == "date unknown [date:low]"


def test_parallel_undated_result_survives_only_for_current_window():
    today = datetime.now(timezone.utc).date()
    date_range = ((today - timedelta(days=30)).isoformat(), today.isoformat())
    payload = {"results": [{
        "title": "Undated", "url": "https://example.com/parallel",
    }]}
    with patch("lib.grounding.http.request", return_value=payload) as request:
        raw, _ = grounding.parallel_search("test", date_range, "fake-key")
    assert request.call_args.kwargs["json_data"]["advanced_settings"]["source_policy"] == {
        "after_date": date_range[0]
    }
    normalized = normalize.normalize_source_items("grounding", raw, *date_range)
    assert len(normalized) == 1
    assert normalized[0].published_at is None
    assert normalized[0].date_confidence == "low"
    assert render._format_date(normalized[0]) == "date unknown [date:low]"


def test_parallel_historical_window_drops_undated_result():
    today = datetime.now(timezone.utc).date()
    date_range = (
        (today - timedelta(days=60)).isoformat(),
        (today - timedelta(days=30)).isoformat(),
    )
    payload = {"results": [{
        "title": "Undated", "url": "https://example.com/parallel",
    }]}
    with patch("lib.grounding.http.request", return_value=payload):
        raw, artifact = grounding.parallel_search("test", date_range, "fake-key")
    assert raw == []
    assert artifact["resultCount"] == 0


def test_parallel_undated_result_survives_utc_midnight_between_stages():
    today = datetime.now(timezone.utc).date()
    date_range = (
        (today - timedelta(days=8)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
    )
    payload = {"results": [{
        "title": "Undated", "url": "https://example.com/parallel",
    }]}
    with patch("lib.grounding.http.request", return_value=payload), \
         patch("lib.grounding.datetime") as retrieval_clock:
        retrieval_clock.now.return_value = datetime.fromisoformat(
            f"{date_range[1]}T23:59:00+00:00"
        )
        raw, _ = grounding.parallel_search("test", date_range, "fake-key")
    normalized = normalize.normalize_source_items("grounding", raw, *date_range)
    assert len(normalized) == 1
    assert normalized[0].published_at is None
    assert normalized[0].date_confidence == "low"


def _titles(items):
    return [item["title"] for item in items]


class TestSerperKeepsUndatedOrganicResults:
    def test_all_undated_organic_results_are_returned(self):
        payload = {
            "organic": [
                {"title": "A", "link": "https://a.example/1", "snippet": "a"},
                {"title": "B", "link": "https://b.example/2", "snippet": "b"},
                {"title": "C", "link": "https://c.example/3", "snippet": "c"},
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.serper_search(
                "OpenAI", DATE_RANGE, "fake-key", count=5
            )
        assert _titles(items) == ["A", "B", "C"]
        assert artifact["resultCount"] == 3
        assert all(item["date"] is None for item in items)

    def test_drops_only_known_out_of_range_dates(self):
        payload = {
            "organic": [
                {"title": "Undated A", "link": "https://a.example/1", "snippet": "a"},
                {
                    "title": "Out of range",
                    "link": "https://b.example/2",
                    "snippet": "b",
                    "date": "Apr 28, 2025",
                },
                {"title": "Undated C", "link": "https://c.example/3", "snippet": "c"},
                {
                    "title": "In range",
                    "link": "https://d.example/4",
                    "snippet": "d",
                    "date": "Mar 15, 2026",
                },
                {"title": "Undated E", "link": "https://e.example/5", "snippet": "e"},
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.serper_search(
                "OpenAI", DATE_RANGE, "fake-key", count=5
            )
        assert _titles(items) == ["Undated A", "Undated C", "In range", "Undated E"]
        assert artifact["resultCount"] == 4
        assert items[2]["date"] == "2026-03-15"
        assert items[0]["date"] is None

    def test_unparseable_date_is_kept(self):
        payload = {
            "organic": [
                {
                    "title": "Garbage date",
                    "link": "https://a.example/1",
                    "snippet": "a",
                    "date": "not a date",
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.serper_search(
                "OpenAI", DATE_RANGE, "fake-key", count=5
            )
        assert _titles(items) == ["Garbage date"]
        assert artifact["resultCount"] == 1
        assert items[0]["date"] is None


class TestBraveKeepsUndatedResults:
    def test_all_undated_results_are_returned(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": "U",
                        "url": "https://u.example/",
                        "description": "u",
                    }
                ]
            }
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.brave_search(
                "OpenAI", DATE_RANGE, "fake-key", count=5
            )
        assert _titles(items) == ["U"]
        assert artifact["resultCount"] == 1
        assert items[0]["date"] is None

    def test_drops_only_known_out_of_range_dates(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": "In range",
                        "url": "https://example.com/article",
                        "description": "ok",
                        "page_age": "2026-03-10T00:00:00",
                    },
                    {
                        "title": "Old",
                        "url": "https://example.com/old",
                        "description": "old",
                        "page_age": "2025-12-10T00:00:00",
                    },
                    {
                        "title": "Undated",
                        "url": "https://example.com/undated",
                        "description": "no date",
                    },
                ]
            }
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.brave_search(
                "test", DATE_RANGE, "fake-key"
            )
        assert _titles(items) == ["In range", "Undated"]
        assert artifact["resultCount"] == 2


class TestExaKeepsUndatedResults:
    def test_all_undated_results_are_returned(self):
        payload = {
            "results": [
                {"title": "U", "url": "https://u.example/", "text": "u"},
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.exa_search(
                "OpenAI", DATE_RANGE, "fake-key", count=5
            )
        assert _titles(items) == ["U"]
        assert artifact["resultCount"] == 1
        assert items[0]["date"] is None

    def test_drops_only_known_out_of_range_dates(self):
        payload = {
            "results": [
                {
                    "title": "In range",
                    "url": "https://example.com/exa",
                    "text": "ok",
                    "publishedDate": "2026-03-15T00:00:00.000Z",
                },
                {
                    "title": "Old",
                    "url": "https://example.com/old-exa",
                    "text": "old",
                    "publishedDate": "2025-12-01T00:00:00.000Z",
                },
                {
                    "title": "Undated",
                    "url": "https://example.com/undated-exa",
                    "text": "no date",
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.exa_search("test", DATE_RANGE, "fake-key")
        assert _titles(items) == ["In range", "Undated"]
        assert artifact["resultCount"] == 2


class TestParallelKeepsUndatedResults:
    def test_all_undated_results_are_returned(self):
        payload = {
            "results": [
                {
                    "title": "U",
                    "url": "https://u.example/",
                    "excerpts": ["u"],
                }
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.parallel_search(
                "OpenAI", LIVE_DATE_RANGE, "fake-key", count=5
            )
        assert _titles(items) == ["U"]
        assert artifact["resultCount"] == 1
        assert items[0]["date"] is None

    def test_drops_only_known_out_of_range_dates(self):
        payload = {
            "results": [
                {
                    "title": "In range",
                    "url": "https://example.com/parallel",
                    "excerpts": ["ok"],
                    "publish_date": f"{LIVE_DATE_RANGE[1]}T00:00:00Z",
                },
                {
                    "title": "Old",
                    "url": "https://example.com/old-parallel",
                    "excerpts": ["old"],
                    "publish_date": "2025-12-01T00:00:00Z",
                },
                {
                    "title": "Undated",
                    "url": "https://example.com/undated-parallel",
                    "excerpts": ["no date"],
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=payload):
            items, artifact = grounding.parallel_search(
                "test", LIVE_DATE_RANGE, "fake-key"
            )
        assert _titles(items) == ["In range", "Undated"]
        assert artifact["resultCount"] == 2
