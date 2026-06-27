"""Tests for the Techmeme source adapter (lib/techmeme.py).

Covers the --json (not --agent) surface choice, header-row filtering,
field mapping, the one-time sync guard, and graceful degradation.
"""

from __future__ import annotations

import pytest

from lib import techmeme


@pytest.fixture(autouse=True)
def _reset_sync_flag():
    techmeme._SYNCED = False
    yield
    techmeme._SYNCED = False


# ---- surface choice ----

def test_search_args_use_json_not_agent():
    args = techmeme._build_search_args("AI agents", 16)
    assert "--json" in args
    # --agent implies --compact, which blanked records pre-PR-1383.
    assert "--agent" not in args
    assert "--compact" not in args
    assert "search" in args and "AI agents" in args


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
            {"num": 1, "source": "techcrunch.com", "headline": "TechCrunch", "link": "http://techcrunch.com/"},
            {"num": 2, "source": "techcrunch.com",
             "headline": "Sakana AI's Fugu claims to rival frontier models",
             "link": "https://www.techmeme.com/260627/p2"},
        ]
    }
    items = techmeme.parse_techmeme_response(resp, query="AI")
    assert len(items) == 1
    assert items[0]["title"].startswith("Sakana AI")
    assert items[0]["url"] == "https://www.techmeme.com/260627/p2"
    assert items[0]["source_name"] == "techcrunch.com"


def test_parse_drops_records_without_link():
    resp = {"results": [{"num": 1, "source": "x.com", "headline": "A real headline sentence here", "link": ""}]}
    assert techmeme.parse_techmeme_response(resp, query="x") == []


# ---- relevance ranking ----

def test_more_relevant_headline_ranks_higher():
    resp = {
        "results": [
            {"num": 1, "source": "a.com", "headline": "Unrelated quarterly earnings report released today", "link": "https://t.co/a"},
            {"num": 2, "source": "b.com", "headline": "New AI agent framework launches for developers", "link": "https://t.co/b"},
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


# ---- sync guard + degradation ----

def test_sync_runs_once_per_process(monkeypatch):
    calls = []
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme.subproc, "run_with_timeout",
                        lambda cmd, timeout: calls.append(cmd) or _FakeProc())
    techmeme._ensure_synced()
    techmeme._ensure_synced()
    sync_calls = [c for c in calls if "sync" in c]
    assert len(sync_calls) == 1


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


class _FakeProc:
    returncode = 0
    stdout = "{}"
    stderr = ""
