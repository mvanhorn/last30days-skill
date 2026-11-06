"""Tests for the arXiv source adapter (lib/arxiv.py).

Covers query construction (quoted phrase + relevance sort), the recency
cutoff that doubles as off-topic gating, field mapping, the native Atom
parser, and graceful degradation on HTTP / parse failures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lib import arxiv, http


ATOM_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "arxiv_atom_llm_inference.xml"


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

def _params(url):
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.hostname == "export.arxiv.org"
    return {k: v[0] for k, v in parse_qs(parts.query).items()}


def test_search_query_is_quoted_phrase_relevance_sorted():
    params = _params(arxiv._build_search_url("AI coding agents", 10))
    assert params["search_query"] == 'all:"AI coding agents"'  # quoted phrase
    assert params["sortBy"] == "relevance"  # NOT submittedDate
    assert params["max_results"] == "10"


def test_search_query_strips_inner_quotes():
    q = arxiv._build_search_query('say "hello" world')
    assert q == 'all:"say hello world"'


def test_unquoted_fallback_conjoins_terms():
    params = _params(arxiv._build_search_url("AI video generation advances", 10, quoted=False))
    assert params["search_query"] == (
        'all:"AI" AND all:"video" AND all:"generation" AND all:"advances"'
    )


def test_unquoted_fallback_quotes_operator_and_colon_terms():
    params = _params(arxiv._build_search_url("AI AND OR title:video", 5, quoted=False))
    assert params["search_query"] == (
        'all:"AI" AND all:"AND" AND all:"OR" AND all:"title:video"'
    )


# ---- native Atom parsing ----

def test_parse_atom_fixture_maps_entry_shape():
    entries = arxiv._parse_atom(ATOM_FIXTURE.read_text())
    assert len(entries) == 3
    first = entries[0]
    assert first["title"] and first["published"]
    assert first["authors"] and first["authors"][0]["name"]
    assert any(link["rel"] == "alternate" for link in first["links"])


def test_parse_atom_fixture_flows_through_parse_response():
    entries = arxiv._parse_atom(ATOM_FIXTURE.read_text())
    items = arxiv.parse_arxiv_response(
        {"results": entries}, query="llm inference",
        today=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )
    assert items
    assert all(item["url"].startswith("https://arxiv.org/abs/") for item in items)


def test_parse_atom_rejects_non_feed_root():
    import xml.etree.ElementTree as ET
    import pytest

    with pytest.raises(ET.ParseError):
        arxiv._parse_atom("<html><body>nope</body></html>")


def test_parse_atom_does_not_expand_external_entities():
    import xml.etree.ElementTree as ET
    import pytest

    doc = (
        '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>'
    )
    with pytest.raises(ET.ParseError):
        arxiv._parse_atom(doc)


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

def test_empty_topic_returns_empty():
    assert arxiv.search_arxiv("   ", "2026-06-01", "2026-06-27") == {"results": []}


def test_parse_handles_non_list_results():
    assert arxiv.parse_arxiv_response({"results": "oops"}, query="x", today=NOW) == []
    assert arxiv.parse_arxiv_response({}, query="x", today=NOW) == []


def test_quote_only_topic_returns_empty():
    # A topic of only quote characters cleans to an empty phrase; don't search.
    assert arxiv.search_arxiv('"', "2026-06-01", "2026-06-27") == {"results": []}


def test_same_day_future_timestamp_is_kept():
    # A paper announced later the same UTC day yields age_days == -1; the
    # one-day grace keeps it rather than dropping it as "future".
    later_today = NOW.replace(hour=23, minute=59).strftime("%Y-%m-%dT%H:%M:00Z")
    resp = {"results": [_entry("Fresh paper", later_today)]}
    items = arxiv.parse_arxiv_response(resp, query="fresh paper", today=NOW.replace(hour=1))
    assert len(items) == 1


def test_as_of_window_anchors_recency_and_drops_later_papers(monkeypatch):
    # An --as-of run ends the window in the past. The adapter must judge
    # recency from that end date, not from the real clock: a paper published
    # after the window cannot appear, and one from just inside it must not be
    # dropped as "older than a year" relative to today.
    from lib import pipeline, schema

    entries = [
        _entry("Window paper", "2025-05-20T12:00:00Z"),
        _entry("End day paper", "2025-06-01T23:30:00Z"),
        _entry("Later paper", "2025-06-02T00:30:00Z"),
        _entry("Much later paper", "2026-01-15T12:00:00Z"),
    ]
    monkeypatch.setattr(
        arxiv, "search_arxiv", lambda *_args, **_kwargs: {"results": entries}
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="paper",
        ranking_query="paper",
        sources=["arxiv"],
    )

    items, _ = pipeline._retrieve_stream_impl(
        topic="paper",
        subquery=subquery,
        source="arxiv",
        config={},
        depth="quick",
        date_range=("2025-05-02", "2025-06-01"),
        runtime=schema.ProviderRuntime(
            reasoning_provider="none",
            planner_model="none",
            rerank_model="none",
        ),
        mock=False,
    )

    assert [item["title"] for item in items] == ["Window paper", "End day paper"]


def _feed(*titles):
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/{i}</id><title>{t}</title>"
        "<published>2026-06-20T00:00:00Z</published></entry>"
        for i, t in enumerate(titles)
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'


def _fake_http(monkeypatch, responder):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        assert kwargs.get("raw") is True
        return responder(url)

    monkeypatch.setattr(arxiv.http, "request", fake_request)
    return calls


def test_fetch_parses_feed(monkeypatch):
    _fake_http(monkeypatch, lambda url: _feed("X"))
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert [e["title"] for e in resp["results"]] == ["X"]


def test_http_error_returns_error(monkeypatch):
    def boom(url):
        raise http.HTTPError("boom", status_code=503)

    calls = _fake_http(monkeypatch, boom)
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [] and "boom" in resp["error"]
    assert len(calls) == 1  # a real failure never triggers the unquoted retry


def test_malformed_xml_returns_error_without_retry(monkeypatch):
    calls = _fake_http(monkeypatch, lambda url: "not xml")
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [] and resp["error"].startswith("atom parse")
    assert len(calls) == 1


def test_empty_body_returns_error_without_retry(monkeypatch):
    calls = _fake_http(monkeypatch, lambda url: "")
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp == {"results": [], "error": "empty response"}
    assert len(calls) == 1


# ---- unquoted-retry on zero results (#908) ----

def test_zero_result_quoted_query_retries_unquoted_and_finds_results(monkeypatch):
    """A natural-language multi-word topic matches nothing as an exact
    phrase, but the unquoted retry finds it -- the fix for #908."""
    def responder(url):
        query = _params(url)["search_query"]
        if query == 'all:"AI video generation advances"':
            return _feed()
        return _feed("AI video generation advances")

    calls = _fake_http(monkeypatch, responder)
    resp = arxiv.search_arxiv("AI video generation advances", "2026-06-01", "2026-06-27")
    assert [e["title"] for e in resp["results"]] == ["AI video generation advances"]
    assert len(calls) == 2
    assert _params(calls[0])["search_query"] == 'all:"AI video generation advances"'
    assert _params(calls[1])["search_query"] == (
        'all:"AI" AND all:"video" AND all:"generation" AND all:"advances"'
    )


def test_zero_result_quoted_query_retry_also_empty_returns_empty(monkeypatch):
    calls = _fake_http(monkeypatch, lambda url: _feed())
    resp = arxiv.search_arxiv("truly obscure nonsense topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == []
    assert "error" not in resp
    assert len(calls) == 2
