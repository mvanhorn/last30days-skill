"""Keyed web backends must not drop undated organic results (issue #928).

Brave freshness, Exa published-date bounds, and Serper ``tbs=cdr`` already
constrain the query server-side. Organic results often omit a parseable
date; treating that as out-of-range emptied the backend. Only a known date
outside the window is dropped.
"""

from unittest.mock import patch

from lib import grounding

DATE_RANGE = ("2026-02-25", "2026-03-27")


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
                    "url": "https://example.com/parallel",
                    "excerpts": ["ok"],
                    "publish_date": "2026-03-15T00:00:00Z",
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
                "test", DATE_RANGE, "fake-key"
            )
        assert _titles(items) == ["In range", "Undated"]
        assert artifact["resultCount"] == 2
