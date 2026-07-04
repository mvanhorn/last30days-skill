"""Tests for the Techmeme source adapter (lib/techmeme.py).

Covers the --json (not --agent) surface choice, header-row filtering,
field mapping, date windowing to the research range, old-binary prose
tolerance, and graceful degradation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lib import techmeme


class _FakeProc:
    returncode = 0
    stdout = "{}"
    stderr = ""

    def __init__(self, stdout: str = "{}", returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


# ---- surface choice ----

def test_search_args_use_json_not_agent():
    args = techmeme._build_search_args("AI agents")
    assert "--json" in args
    # --agent implies --compact, which blanked records pre-PR-1383.
    assert "--agent" not in args
    assert "--compact" not in args
    # Techmeme `search` has no result-limit flag; --max-results breaks it.
    assert "--max-results" not in args
    assert "search" in args and "AI agents" in args


def test_search_invokes_only_search_no_sync(monkeypatch):
    """search_techmeme must issue exactly one subprocess call -- the search --
    and never a `sync` (search hits Techmeme's live archive, not the cache)."""
    calls = []

    def fake_run(cmd, timeout):
        calls.append(list(cmd))
        return _FakeProc(stdout=json.dumps([
            {"num": 1, "source": "a.com",
             "headline": "A real in-window headline about the topic",
             "link": "https://t.co/a", "date": "2026-06-15"},
        ]))

    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme.subproc, "run_with_timeout", fake_run)
    out = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27")
    assert len(out["results"]) == 1
    assert calls == [[techmeme.CLI_BIN, "search", "topic", "--json"]]
    assert not any("sync" in c for c in calls)


# ---- date windowing ----

def _search_with_records(monkeypatch, records, from_date="2026-06-01", to_date="2026-06-27"):
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(
        techmeme.subproc, "run_with_timeout",
        lambda cmd, timeout: _FakeProc(stdout=json.dumps(records)),
    )
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
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(
        techmeme.subproc, "run_with_timeout",
        lambda cmd, timeout: _FakeProc(stdout=json.dumps([
            {"num": 1, "source": "a.com",
             "headline": "Stale story from the deep archive years back",
             "link": "https://t.co/old", "date": "2022-12-02"},
        ])),
    )
    techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27")
    assert "dropped 1 records outside 2026-06-01..2026-06-27" in logs


def test_undated_records_kept_and_never_stamped_today(monkeypatch):
    """Old binaries emit no date key; unparseable dates come through as "" or
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


# ---- old-binary prose tolerance ----

def test_no_results_prose_parses_as_empty_without_error(monkeypatch):
    """Old binaries print `No results for "q"` prose to stdout with exit 0 in
    JSON mode. That is a zero-hit response, not a decode failure."""
    logs = []
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(
        techmeme.subproc, "run_with_timeout",
        lambda cmd, timeout: _FakeProc(stdout='No results for "whatever query"\n'),
    )
    resp = techmeme._run_cli([techmeme.CLI_BIN, "search", "whatever query", "--json"],
                             timeout=techmeme.SEARCH_TIMEOUT)
    assert resp == {"results": []}
    assert "error" not in resp
    assert not any("decode" in m.lower() for m in logs)


def test_malformed_stdout_still_yields_decode_error(monkeypatch):
    """Genuinely malformed non-JSON stdout (not the prose sentinel) keeps the
    existing decode-error envelope."""
    logs = []
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(
        techmeme.subproc, "run_with_timeout",
        lambda cmd, timeout: _FakeProc(stdout="garbage <<<"),
    )
    resp = techmeme._run_cli([techmeme.CLI_BIN, "search", "topic", "--json"],
                             timeout=techmeme.SEARCH_TIMEOUT)
    assert resp["results"] == []
    assert "json decode" in resp.get("error", "")
    assert any("decode" in m.lower() for m in logs)


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


# ---- envelope tolerance ----

def test_coerce_list_handles_bare_array_and_wrapped():
    assert techmeme._coerce_list([{"a": 1}]) == [{"a": 1}]
    assert techmeme._coerce_list({"results": [{"a": 1}]}) == [{"a": 1}]
    assert techmeme._coerce_list({"nope": 1}) == []


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
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_run_cli", lambda cmd, timeout: {"results": list(records)})
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
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_run_cli",
                        lambda cmd, timeout: {"results": stale + fresh})
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
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    # Undated arrive first in CLI order; dated in-window must still win the cap.
    monkeypatch.setattr(techmeme, "_run_cli",
                        lambda cmd, timeout: {"results": undated + dated})
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
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(techmeme, "_run_cli", lambda cmd, timeout: {"results": records})
    out = techmeme.search_techmeme("topic", "2026-06-01", "2026-06-27", depth="quick")
    assert len(out["results"]) == cap
    assert any("date windowing inactive" in m for m in logs)


def test_windowing_inactive_hint_absent_when_dates_present(monkeypatch):
    """The hint must not fire when the binary emits dates -- even if every
    dated record is out of window (windowing IS active there)."""
    logs = []
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_log", lambda msg: logs.append(msg))
    monkeypatch.setattr(techmeme, "_run_cli", lambda cmd, timeout: {"results": [
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


def test_binary_absent_returns_empty(monkeypatch):
    monkeypatch.setattr(techmeme.shutil, "which", lambda _bin: None)
    resp = techmeme.search_techmeme("anything", "2026-06-01", "2026-06-27")
    assert resp["results"] == []
    assert "error" in resp


def test_empty_topic_returns_empty():
    assert techmeme.search_techmeme("  ", "2026-06-01", "2026-06-27") == {"results": []}


def test_parse_handles_non_list_results():
    assert techmeme.parse_techmeme_response({"results": "oops"}, query="x") == []
    assert techmeme.parse_techmeme_response({}, query="x") == []
