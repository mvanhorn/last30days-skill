"""Opt-in WebRouter web backend (``--web-backend webrouter``)."""

import json
import os
import stat
import sys
import threading
from datetime import date
from unittest.mock import patch

import pytest

from lib import backends, grounding, health, pipeline, subproc, webrouter

WINDOW = ("2026-10-27", "2026-11-26")


def _result(stdout="", returncode=0, stderr=""):
    return subproc.SubprocResult(returncode=returncode, stdout=stdout, stderr=stderr)


def _payload(provider, rows):
    return json.dumps({"provider": provider, "query": "q", "results": rows})


def _row(url, published_date, title="Title", content="Body"):
    return {"url": url, "title": title, "content": content, "published_date": published_date}


@pytest.fixture
def on_path(monkeypatch):
    monkeypatch.setattr(webrouter, "is_available", lambda: True)
    monkeypatch.setattr(webrouter, "_today", lambda: date(2026, 11, 26))


def test_keeps_only_dated_http_results_inside_the_window(on_path):
    rows = [
        _row("https://example.com/in", "2026-11-20T08:00:00", content="x" * 900),
        _row("https://example.com/old", "2026-09-01T00:00:00"),
        _row("https://example.com/undated", None),
        _row("ftp://example.com/file", "2026-11-21"),
        _row("https://example.com/z", "2026-11-25T20:03:27.000Z", title=""),
    ]
    with patch.object(subproc, "run_with_timeout", return_value=_result(_payload("you-mcp", rows))):
        items, artifact = webrouter.search("topic", WINDOW)
    assert [i["url"] for i in items] == ["https://example.com/in", "https://example.com/z"]
    assert [i["date"] for i in items] == ["2026-11-20", "2026-11-25"]
    assert [i["id"] for i in items] == ["WR1", "WR2"]
    assert len(items[0]["snippet"]) == 500
    assert items[1]["title"] == "example.com"
    assert items[0]["why_relevant"] == "WebRouter web search (you-mcp)"
    assert artifact == {
        "label": "webrouter", "provider": "you-mcp",
        "webSearchQueries": ["topic"], "resultCount": 2,
    }


def test_command_is_keyless_bounded_and_keeps_the_query_out_of_flag_parsing(on_path, monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "dummy-exa")
    monkeypatch.setenv("YOU_API_KEY", "dummy-you")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _result(_payload("you-mcp", [_row("https://example.com/a", "2026-11-20")]))

    with patch.object(subproc, "run_with_timeout", side_effect=run):
        webrouter.search("--help me", WINDOW, count=3)
    cmd, kwargs = calls[0]
    assert cmd[:3] == ["webrouter", "search", "--provider"]
    assert cmd[3] == "you-mcp"
    assert cmd[-2:] == ["--", "--help me"]
    assert ["--max-results", "3"] == cmd[4:6]
    assert "--no-data-apis" in cmd
    assert cmd[cmd.index("--recency") + 1] == "month"
    assert not any(name.endswith("_KEY") for name in kwargs["env"])
    assert kwargs["capture_limit_bytes"] == webrouter._MAX_OUTPUT_BYTES


def test_falls_through_to_exa_when_you_fails_or_finds_nothing_dated(on_path):
    failure = _result(returncode=1, stderr='{"error": {"type": "RateLimitError", "message": "slow down"}}')
    undated = _result(_payload("you-mcp", [_row("https://example.com/u", None)]))
    exa = _result(_payload("exa-mcp", [_row("https://example.com/e", "2026-11-24")]))
    for first in (failure, undated):
        with patch.object(subproc, "run_with_timeout", side_effect=[first, exa]) as run:
            items, artifact = webrouter.search("topic", WINDOW)
        assert [c.args[0][3] for c in run.call_args_list] == ["you-mcp", "exa-mcp"]
        assert artifact["provider"] == "exa-mcp"
        assert items[0]["url"] == "https://example.com/e"


def test_raises_when_every_backend_fails_and_returns_empty_when_none_has_results(on_path):
    responses = [_result(returncode=1, stderr="boom"), _result("not json")]
    with patch.object(subproc, "run_with_timeout", side_effect=responses):
        with pytest.raises(RuntimeError, match="you-mcp: exit 1; exa-mcp: unreadable JSON"):
            webrouter.search("topic", WINDOW)
    empty = _result(_payload("x", []))
    with patch.object(subproc, "run_with_timeout", side_effect=[empty, empty]):
        items, artifact = webrouter.search("topic", WINDOW)
    assert items == [] and artifact["resultCount"] == 0


def test_timeout_and_spawn_failure_count_as_backend_failures(on_path):
    responses = [subproc.SubprocTimeout("too slow"), FileNotFoundError("gone")]
    with patch.object(subproc, "run_with_timeout", side_effect=responses):
        with pytest.raises(RuntimeError, match="timed out: too slow.*could not start webrouter"):
            webrouter.search("topic", WINDOW)


def test_cancelled_or_expired_run_stops_before_spawning(on_path):
    cancel = threading.Event()
    cancel.set()
    with patch.object(subproc, "run_with_timeout") as run:
        assert webrouter.search("topic", WINDOW, cancel=cancel)[0] == []
        assert webrouter.search("topic", WINDOW, deadline_monotonic=0.0)[0] == []
    run.assert_not_called()


def test_missing_cli_raises_with_the_install_command(monkeypatch):
    monkeypatch.setattr(webrouter, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="uv tool install webrouter"):
        webrouter.search("topic", WINDOW)


@pytest.mark.parametrize("start,expected", [
    ("2026-11-25", "day"), ("2026-11-19", "week"), ("2026-10-27", "month"),
    ("2026-01-01", "year"), ("2024-01-01", None), ("not-a-date", None),
])
def test_recency_is_the_narrowest_period_covering_the_window(monkeypatch, start, expected):
    monkeypatch.setattr(webrouter, "_today", lambda: date(2026, 11, 26))
    assert webrouter._recency((start, "2026-11-26")) == expected


@pytest.mark.skipif(os.name == "nt", reason="POSIX shebang script")
def test_runs_a_real_cli_on_path_without_passing_keys(tmp_path, monkeypatch):
    script = tmp_path / "webrouter"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "keyed = any(k.endswith('_API_KEY') for k in os.environ)\n"
        "print(json.dumps({'results': [{'url': 'https://example.com/' + sys.argv[-1],\n"
        "    'title': 'keyed' if keyed else 'keyless', 'published_date': '2026-11-20'}]}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("EXA_API_KEY", "dummy-exa")
    monkeypatch.setattr(webrouter, "_today", lambda: date(2026, 11, 26))
    items, artifact = webrouter.search("topic", WINDOW)
    assert items[0]["url"] == "https://example.com/topic"
    assert items[0]["title"] == "keyless"
    assert artifact["provider"] == "you-mcp"


def test_grounding_dispatches_explicit_pin_with_deadline_and_never_auto_selects():
    cancel = threading.Event()
    with patch("lib.grounding.webrouter.search", return_value=([], {})) as search:
        grounding.web_search("topic", WINDOW, {}, backend="webrouter", deadline_monotonic=99.0, cancel=cancel)
    search.assert_called_once_with("topic", WINDOW, deadline_monotonic=99.0, cancel=cancel)
    with patch("lib.grounding.webrouter.search") as search, \
         patch("lib.grounding.web_search_keyless.keyless_search", return_value=([], {})):
        grounding.web_search("topic", WINDOW, {}, backend="auto")
    search.assert_not_called()


def test_backend_is_opt_in_and_gated_on_the_cli(monkeypatch):
    spec = next(s for s in backends.get_descriptor("web").backends if s.name == "webrouter")
    assert spec.opt_in is True and spec.paid is False
    assert backends.resolve("web", {}).active_backend == "keyless"

    monkeypatch.setattr(webrouter, "is_available", lambda: True)
    pinned = backends.resolve("web", {}, pin="webrouter")
    assert pinned.active_backend == "webrouter"
    assert "will use: webrouter" in pinned.summary

    monkeypatch.setattr(webrouter, "is_available", lambda: False)
    finding = next(f for f in backends.resolve("web", {}, pin="webrouter").findings if f.name == "webrouter")
    assert finding.status == health.MISSING
    assert finding.prescription == "uv tool install webrouter"


def test_pipeline_pin_enables_grounding_on_a_native_search_host(on_path):
    payload = _payload("you-mcp", [_row("https://example.com/update", "2026-08-01", title="Test topic update")])
    with patch.object(subproc, "run_with_timeout", return_value=_result(payload)), \
         patch.object(webrouter, "_today", lambda: date(2026, 8, 26)):
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "auto", "LAST30DAYS_NATIVE_SEARCH": "1"},
            depth="quick",
            requested_sources=["grounding"],
            web_backend="webrouter",
            as_of_date="2026-08-26",
        )
    assert "grounding" not in report.errors_by_source
    assert [i.published_at for i in report.items_by_source["grounding"]] == ["2026-08-01"]
