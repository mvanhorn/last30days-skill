"""Tests for the arXiv source adapter (lib/arxiv.py).

Covers query construction (quoted phrase + relevance sort), the recency
cutoff that doubles as off-topic gating, field mapping, and graceful
degradation when the binary is absent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lib import arxiv


NOW = datetime(2026, 6, 27, tzinfo=timezone.utc)


def _entry(title, published, summary="An abstract.", authors=("Ada Lovelace",), abs_url="https://arxiv.org/abs/2606.00001v1"):
    return {
        "id": "http://arxiv.org/abs/2606.00001v1",
        "title": title,
        "summary": summary,
        "published": published,
        "authors": [{"name": a} for a in authors],
        "categories": [{"term": "cs.AI"}],
        "links": [
            {"rel": "alternate", "href": abs_url, "type": "text/html"},
            {"rel": "related", "href": "https://arxiv.org/pdf/2606.00001v1", "title": "pdf"},
        ],
    }


# ---- query construction ----

def test_search_query_is_quoted_phrase_relevance_sorted():
    args = arxiv._build_search_args("AI coding agents", 10)
    joined = " ".join(args)
    assert '--search-query' in args
    assert 'all:"AI coding agents"' in args  # quoted phrase
    assert "--sort-by" in args and "relevance" in args  # NOT submittedDate
    assert "submittedDate" not in joined


def test_search_query_strips_inner_quotes():
    q = arxiv._build_search_query('say "hello" world')
    assert q == 'all:"say hello world"'


# ---- envelope extraction ----

def test_extract_entries_handles_nested_results_envelope():
    data = {"meta": {"source": "live"}, "results": {"entries": [{"title": "X"}]}}
    entries = arxiv._extract_entries(data)
    assert len(entries) == 1 and entries[0]["title"] == "X"


# ---- happy path + field mapping ----

def test_happy_path_maps_fields():
    recent = (NOW - timedelta(days=10)).strftime("%Y-%m-%dT12:00:00Z")
    resp = {"results": [_entry("Agent Memory as a Database", recent, authors=("A", "B"))]}
    items = arxiv.parse_arxiv_response(resp, query="agent memory", today=NOW)
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "Agent Memory as a Database"
    assert it["url"] == "https://arxiv.org/abs/2606.00001v1"  # alternate, not pdf
    assert it["date"] == (NOW - timedelta(days=10)).date().isoformat()
    assert it["authors"] == ["A", "B"]
    assert it["author"] == "A et al."  # multi-author label


def test_url_prefers_alternate_over_pdf():
    recent = (NOW - timedelta(days=5)).strftime("%Y-%m-%dT12:00:00Z")
    resp = {"results": [_entry("T", recent)]}
    items = arxiv.parse_arxiv_response(resp, query="t", today=NOW)
    assert "/pdf/" not in items[0]["url"]
    assert "/abs/" in items[0]["url"]


# ---- recency cutoff (also off-topic gating) ----

def test_recency_cutoff_drops_stale_paper():
    stale = (NOW - timedelta(days=arxiv.RECENCY_DAYS + 30)).strftime("%Y-%m-%dT12:00:00Z")
    resp = {"results": [_entry("Old Paper", stale)]}
    items = arxiv.parse_arxiv_response(resp, query="old paper", today=NOW)
    assert items == []


def test_off_topic_control_drops_via_recency():
    # The "Golden State Warriors" keyword match is a 2017 stats paper -- the
    # recency cutoff is what keeps arXiv quiet on non-research topics.
    old = "2017-06-12T12:00:00Z"
    resp = {"results": [_entry("Do Steph Curry and Klay Thompson Have Hot Hands?", old)]}
    items = arxiv.parse_arxiv_response(resp, query="Golden State Warriors", today=NOW)
    assert items == []


def test_unparseable_or_missing_date_is_dropped():
    resp = {"results": [_entry("No Date", None), _entry("Bad Date", "not-a-date")]}
    items = arxiv.parse_arxiv_response(resp, query="x", today=NOW)
    assert items == []


def test_future_dated_entry_is_dropped():
    future = (NOW + timedelta(days=5)).strftime("%Y-%m-%dT12:00:00Z")
    resp = {"results": [_entry("Future", future)]}
    items = arxiv.parse_arxiv_response(resp, query="future", today=NOW)
    assert items == []


# ---- error / degradation paths ----

def test_binary_absent_returns_empty(monkeypatch):
    monkeypatch.setattr(arxiv.shutil, "which", lambda _bin: None)
    resp = arxiv.search_arxiv("anything", "2026-06-01", "2026-06-27")
    assert resp["results"] == []
    assert "error" in resp


def test_empty_topic_returns_empty():
    assert arxiv.search_arxiv("   ", "2026-06-01", "2026-06-27") == {"results": []}


def test_parse_handles_non_list_results():
    assert arxiv.parse_arxiv_response({"results": "oops"}, query="x", today=NOW) == []
    assert arxiv.parse_arxiv_response({}, query="x", today=NOW) == []
