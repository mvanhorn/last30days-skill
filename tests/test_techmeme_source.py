"""Tests for the Techmeme source adapter (lib/techmeme.py).

Covers the native search-page client (URL, ported HTML parser against a
recorded fixture), header-row filtering, field mapping, date windowing to the
research range, and graceful degradation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import http, techmeme


SEARCH_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "techmeme_search_nvidia.html"


# ---- request + native parser ----

def test_search_url_targets_archive_search_without_limit():
    parts = urlsplit(techmeme._build_search_url("AI agents"))
    assert (parts.scheme, parts.hostname, parts.path) == (
        "https", "www.techmeme.com", "/search/d3results.jsp",
    )
    assert parse_qs(parts.query) == {"q": ["AI agents"], "wm": ["false"]}


def test_search_issues_exactly_one_request(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        assert kwargs.get("raw") is True
        return SEARCH_FIXTURE.read_text()

    monkeypatch.setattr(techmeme.http, "request", fake_request)
    out = techmeme.search_techmeme("nvidia", "2026-10-07", "2026-11-06")
    assert len(calls) == 1 and calls[0][0] == "GET"
    assert out["results"]


def test_parse_search_page_fixture():
    records, markup_shift = techmeme.parse_search_page(SEARCH_FIXTURE.read_text())
    assert not markup_shift
    assert len(records) == 10
    first = records[0]
    assert first["num"] == 1
    assert first["source"] == "bloomberg.com"
    assert first["link"].startswith("https://www.bloomberg.com/news/articles/2026-11-06/")
    assert first["headline"].startswith("Sources: Nvidia is requiring")
    assert first["date"] == "2026-11-06"
    assert all(r["date"] for r in records)


def test_parse_search_page_no_results_is_empty():
    assert techmeme.parse_search_page("<html><body>No results</body></html>") == ([], False)


@pytest.mark.parametrize("text,expected", [
    ("Nov 6, 2026, 6:30 AM", "2026-11-06"),
    ("November 6, 2026", "2026-11-06"),
    ("garbage", ""),
])
def test_parse_date_layouts(text, expected):
    assert techmeme._parse_date(text) == expected


def test_http_error_returns_error(monkeypatch):
    def boom(method, url, **kwargs):
        raise http.HTTPError("boom", status_code=503)

    monkeypatch.setattr(techmeme.http, "request", boom)
    resp = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [] and "boom" in resp["error"]


# ---- date windowing ----

def _search_with_records(monkeypatch, records, from_date="2026-06-01", to_date="2026-06-27"):
    monkeypatch.setattr(techmeme, "_fetch", lambda url, timeout: {"results": list(records)})
    return techmeme.search_techmeme("topic", from_date, to_date)


def test_in_window_record_kept_with_real_date(monkeypatch):
    out = _search_with_records(monkeypatch, [
        {"num": 1, "source": "a.com",
         "headline": "Fresh in-window story about the research topic",
         "link": "https://t.co/a", "date": "2026-06-15"},
    ])
    assert len(out["results"]) == 1
    items = techmeme.parse_techmeme_response(out, query="topic")
    assert len(items) == 1
    # The item carries the record's real date, never today's.
    assert items[0]["date"] == "2026-06-15"


def test_out_of_window_record_dropped(monkeypatch):
    out = _search_with_records(monkeypatch, [
        {"num": 1, "source": "a.com",
         "headline": "Stale Parler acquisition story from years ago",
         "link": "https://t.co/old", "date": "2022-12-02"},
        {"num": 2, "source": "b.com",
         "headline": "Fresh in-window story about the research topic",
         "link": "https://t.co/new", "date": "2026-06-15"},
    ])
    links = [r["link"] for r in out["results"]]
    assert links == ["https://t.co/new"]


def test_zero_in_window_records_means_zero_results(monkeypatch):
    """No keep-all fallback: staleness is the bug, so all-stale means empty."""
    out = _search_with_records(monkeypatch, [
        {"num": 1, "source": "a.com",
         "headline": "Stale story number one from the archive",
         "link": "https://t.co/1", "date": "2022-12-02"},
        {"num": 2, "source": "b.com",
         "headline": "Stale story number two from the archive",
         "link": "https://t.co/2", "date": "2023-08-24"},
    ])
    assert out["results"] == []


def test_window_endpoints_inclusive(monkeypatch):
    """Records dated exactly from_date or to_date survive the window; a
    strict-< regression would silently shave both edges."""
    out = _search_with_records(monkeypatch, [
        {"num": 1, "source": "a.com",
         "headline": "Story published exactly on the window start date",
         "link": "https://t.co/start", "date": "2026-06-01"},
        {"num": 2, "source": "b.com",
         "headline": "Story published exactly on the window end date",
         "link": "https://t.co/end", "date": "2026-06-27"},
    ])
    assert [r["link"] for r in out["results"]] == ["https://t.co/start", "https://t.co/end"]


def test_non_string_and_whitespace_dates(monkeypatch):
    """Non-string date values (int/null) are treated as undated and kept;
    a valid ISO date with surrounding whitespace still parses."""
    out = _search_with_records(monkeypatch, [
        {"num": 1, "source": "a.com",
         "headline": "Record carrying an integer where the date belongs",
         "link": "https://t.co/int", "date": 20260615},
        {"num": 2, "source": "b.com",
         "headline": "Record carrying an explicit null date value",
         "link": "https://t.co/null", "date": None},
        {"num": 3, "source": "c.com",
         "headline": "Record with whitespace padded valid ISO date",
         "link": "https://t.co/ws", "date": "  2026-06-15  "},
    ])
    assert len(out["results"]) == 3
    items = techmeme.parse_techmeme_response(out, query="record")
    by_url = {it["url"]: it["date"] for it in items}
    assert by_url["https://t.co/int"] is None
    assert by_url["https://t.co/null"] is None
    assert by_url["https://t.co/ws"] == "2026-06-15"


def test_dropped_records_logged(monkeypatch):
    logs = []
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(techmeme, "_fetch", lambda url, timeout: {"results": [
        {"num": 1, "source": "a.com",
         "headline": "Stale story from the deep archive years back",
         "link": "https://t.co/old", "date": "2022-12-02"},
    ]})
    techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27")
    assert "dropped 1 records outside 2026-06-01..2026-06-27" in logs


def test_undated_records_kept_and_never_stamped_today(monkeypatch):
    """Records may lack a date key; unparseable dates come through as "" or
    junk. All are kept by the window, and parse yields date None -- never
    today's date."""
    out = _search_with_records(monkeypatch, [
        {"num": 1, "source": "a.com",
         "headline": "Headline with no date key from an old binary",
         "link": "https://t.co/nodate"},
        {"num": 2, "source": "b.com",
         "headline": "Headline whose date was unparseable upstream",
         "link": "https://t.co/empty", "date": ""},
        {"num": 3, "source": "c.com",
         "headline": "Headline carrying non ISO junk in the date",
         "link": "https://t.co/junk", "date": "May 22, 2025"},
    ])
    assert len(out["results"]) == 3
    items = techmeme.parse_techmeme_response(out, query="headline")
    assert len(items) == 3
    today = datetime.now(timezone.utc).date().isoformat()
    for item in items:
        assert item["date"] is None
        assert item["date"] != today


# ---- header-row filtering ----

def test_story_headline_accepts_sentence():
    assert techmeme._is_story_headline("OpenAI ships a new coding agent today", "techcrunch.com")


def test_story_headline_rejects_publication_name_rows():
    # Short, publication-name-only rows are section headers, not stories.
    assert not techmeme._is_story_headline("TechCrunch", "techcrunch.com")
    assert not techmeme._is_story_headline("New York Times", "nytimes.com")


def test_parse_drops_header_rows_keeps_stories():
    resp = {
        "results": [
            {"num": 1, "source": "techcrunch.com", "headline": "TechCrunch",
             "link": "http://techcrunch.com/", "date": "2026-06-27"},
            {"num": 2, "source": "techcrunch.com",
             "headline": "Sakana AI's Fugu claims to rival frontier models",
             "link": "https://www.techmeme.com/260627/p2", "date": "2026-06-27"},
        ]
    }
    items = techmeme.parse_techmeme_response(resp, query="AI")
    assert len(items) == 1
    assert items[0]["title"].startswith("Sakana AI")
    assert items[0]["url"] == "https://www.techmeme.com/260627/p2"
    assert items[0]["source_name"] == "techcrunch.com"
    assert items[0]["date"] == "2026-06-27"


def test_parse_drops_records_without_link():
    resp = {"results": [{"num": 1, "source": "x.com", "headline": "A real headline sentence here",
                         "link": "", "date": "2026-06-27"}]}
    assert techmeme.parse_techmeme_response(resp, query="x") == []


# ---- relevance ranking ----

def test_more_relevant_headline_ranks_higher():
    resp = {
        "results": [
            {"num": 1, "source": "a.com", "headline": "Unrelated quarterly earnings report released today",
             "link": "https://t.co/a", "date": "2026-06-20"},
            {"num": 2, "source": "b.com", "headline": "New AI agent framework launches for developers",
             "link": "https://t.co/b", "date": "2026-06-20"},
        ]
    }
    items = techmeme.parse_techmeme_response(resp, query="AI agent framework")
    by_url = {it["url"]: it["relevance"] for it in items}
    assert by_url["https://t.co/b"] > by_url["https://t.co/a"]


# ---- depth cap + degradation ----

@pytest.mark.parametrize("depth,cap", [("quick", 8), ("default", 16), ("deep", 30)])
def test_depth_cap_truncates_client_side(monkeypatch, depth, cap):
    """Techmeme `search` has no limit flag, so the depth cap is applied after
    windowing -- regression guard for the other half of the --max-results fix.
    All records are in-window, so the cap alone decides the count."""
    records = [
        {"num": i, "source": "x.com", "headline": f"A real headline sentence number {i}",
         "link": f"https://t.co/{i}", "date": "2026-06-15"}
        for i in range(cap + 12)
    ]
    monkeypatch.setattr(techmeme, "_fetch", lambda url, timeout: {"results": list(records)})
    out = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27", depth=depth)
    assert len(out["results"]) == cap


def test_depth_cap_applies_after_windowing(monkeypatch):
    """Stale records must not consume cap slots: with cap in-window records
    plus stale ones interleaved ahead of them, every in-window record
    survives."""
    cap = techmeme.DEPTH_CONFIG["quick"]
    stale = [
        {"num": i, "source": "old.com", "headline": f"Stale archive headline number {i}",
         "link": f"https://t.co/old{i}", "date": "2022-01-10"}
        for i in range(cap)
    ]
    fresh = [
        {"num": 100 + i, "source": "new.com", "headline": f"Fresh in window headline number {i}",
         "link": f"https://t.co/new{i}", "date": "2026-06-15"}
        for i in range(cap)
    ]
    monkeypatch.setattr(techmeme, "_fetch",
                        lambda url, timeout: {"results": stale + fresh})
    out = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27", depth="quick")
    links = [r["link"] for r in out["results"]]
    assert links == [f"https://t.co/new{i}" for i in range(cap)]


def test_dated_records_take_cap_slots_before_undated(monkeypatch):
    """Undated archive hits must never evict confirmed in-window stories:
    dated in-window records fill cap slots first, undated fill the rest."""
    cap = techmeme.DEPTH_CONFIG["quick"]
    undated = [
        {"num": i, "source": "old.com", "headline": f"Undated old binary headline number {i}",
         "link": f"https://t.co/u{i}"}
        for i in range(cap)
    ]
    dated = [
        {"num": 100 + i, "source": "new.com", "headline": f"Dated in window headline number {i}",
         "link": f"https://t.co/d{i}", "date": "2026-06-15"}
        for i in range(cap)
    ]
    # Undated arrive first in CLI order; dated in-window must still win the cap.
    monkeypatch.setattr(techmeme, "_fetch",
                        lambda url, timeout: {"results": undated + dated})
    out = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27", depth="quick")
    links = [r["link"] for r in out["results"]]
    assert links == [f"https://t.co/d{i}" for i in range(cap)]


def test_all_undated_flood_capped_with_windowing_inactive_hint(monkeypatch):
    """The realistic old-binary case: every record undated. The cap still
    applies, and a windowing-inactive hint is logged."""
    cap = techmeme.DEPTH_CONFIG["quick"]
    logs = []
    records = [
        {"num": i, "source": "x.com", "headline": f"Undated archive headline number {i}",
         "link": f"https://t.co/{i}"}
        for i in range(cap + 12)
    ]
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(techmeme, "_fetch", lambda url, timeout: {"results": records})
    out = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27", depth="quick")
    assert len(out["results"]) == cap
    assert any("date windowing inactive" in m for m in logs)


def test_windowing_inactive_hint_absent_when_dates_present(monkeypatch):
    """The hint must not fire when the binary emits dates -- even if every
    dated record is out of window (windowing IS active there)."""
    logs = []
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(techmeme, "_fetch", lambda url, timeout: {"results": [
        {"num": 1, "source": "a.com", "headline": "Stale but properly dated archive story",
         "link": "https://t.co/old", "date": "2022-12-02"},
        {"num": 2, "source": "b.com", "headline": "Undated companion record from mixed output",
         "link": "https://t.co/mixed"},
    ]})
    techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27")
    assert not any("date windowing inactive" in m for m in logs)


@pytest.mark.parametrize("words,expected", [(3, False), (4, True)])
def test_story_headline_word_count_boundary(words, expected):
    headline = " ".join(["word"] * words)
    assert techmeme._is_story_headline(headline, "x.com") is expected


def test_story_headline_rejects_when_equal_to_source():
    # A >=4-word headline that exactly equals its source is still a header row.
    assert not techmeme._is_story_headline("the daily example tribune", "the daily example tribune")


def test_empty_topic_returns_empty():
    assert techmeme.search_techmeme("  ", "2026-06-01", "2026-06-27") == {"results": []}


def test_parse_handles_non_list_results():
    assert techmeme.parse_techmeme_response({"results": "oops"}, query="x") == []
    assert techmeme.parse_techmeme_response({}, query="x") == []
