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


def test_unquoted_fallback_search_args_conjoin_terms_at_command_boundary():
    args = arxiv._build_search_args("AI video generation advances", 10, quoted=False)
    assert args == [
        "arxiv-pp-cli",
        "query",
        "--search-query",
        'all:"AI" AND all:"video" AND all:"generation" AND all:"advances"',
        "--sort-by",
        "relevance",
        "--max-results",
        "10",
        "--agent",
    ]


def test_unquoted_fallback_quotes_operator_and_colon_terms_at_command_boundary():
    args = arxiv._build_search_args("AI AND OR title:video", 5, quoted=False)
    assert args[args.index("--search-query") + 1] == (
        'all:"AI" AND all:"AND" AND all:"OR" AND all:"title:video"'
    )


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


class _Proc:
    def __init__(self, rc, out, err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_run_cli_flattens_nested_envelope(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    payload = '{"meta":{},"results":{"entries":[{"title":"X"}]}}'
    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", lambda cmd, timeout: _Proc(0, payload))
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [{"title": "X"}]


def test_run_cli_nonzero_exit_returns_error(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", lambda cmd, timeout: _Proc(1, "", "boom\nmore"))
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [] and "boom" in resp["error"]


def test_run_cli_bad_json_returns_error(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", lambda cmd, timeout: _Proc(0, "not json"))
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [] and "error" in resp


def test_empty_stdout_returns_error_without_retry(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        return _Proc(0, "")

    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", fake_run)
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp == {"results": [], "error": "empty stdout"}
    assert len(calls) == 1


def test_unrecognized_json_returns_error_without_retry(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        return _Proc(0, '{"status":"ok"}')

    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", fake_run)
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp == {"results": [], "error": "unrecognized JSON response"}
    assert len(calls) == 1


def test_recognized_empty_list_retries(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        return _Proc(0, "[]")

    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", fake_run)
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp == {"results": []}
    assert len(calls) == 2


# ---- unquoted-retry on zero results (#908) ----

def test_zero_result_quoted_query_retries_unquoted_and_finds_results(monkeypatch):
    """A natural-language multi-word topic matches nothing as an exact
    phrase, but the unquoted retry finds it -- the fix for #908."""
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        query = cmd[cmd.index("--search-query") + 1]
        if query == 'all:"AI video generation advances"':
            return _Proc(0, '{"results":{"entries":[]}}')
        return _Proc(0, '{"results":{"entries":[{"title":"AI video generation advances"}]}}')

    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", fake_run)
    resp = arxiv.search_arxiv("AI video generation advances", "2026-06-01", "2026-06-27")
    assert resp["results"] == [{"title": "AI video generation advances"}]
    assert len(calls) == 2
    assert 'all:"AI video generation advances"' in calls[0]
    assert 'all:"AI" AND all:"video" AND all:"generation" AND all:"advances"' in calls[1]


def test_zero_result_quoted_query_retry_also_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        return _Proc(0, '{"results":{"entries":[]}}')

    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", fake_run)
    resp = arxiv.search_arxiv("truly obscure nonsense topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == []
    assert "error" not in resp
    assert len(calls) == 2


def test_real_cli_error_does_not_trigger_unquoted_retry(monkeypatch):
    """A genuine failure (nonzero exit) must not retry -- only a clean
    zero-result success should (R6)."""
    monkeypatch.setattr(arxiv, "_is_available", lambda: True)
    calls = []

    def fake_run(cmd, timeout):
        calls.append(cmd)
        return _Proc(1, "", "boom")

    monkeypatch.setattr(arxiv.subproc, "run_with_timeout", fake_run)
    resp = arxiv.search_arxiv("topic", "2026-06-01", "2026-06-27")
    assert resp["results"] == [] and "boom" in resp["error"]
    assert len(calls) == 1
