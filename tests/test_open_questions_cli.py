"""Open Questions on the command line: the envelope flag, the block, the per-topic file."""

import json
import sys
from datetime import date, timedelta
from unittest import mock

import pytest

import last30days as cli
from lib import open_questions, render

BASE = ["--mock", "--emit=compact", "--save-dir", "", "--no-browser-cookies"]
SOON = (date.today() + timedelta(days=20)).isoformat()
LATER = (date.today() + timedelta(days=45)).isoformat()


def _run(monkeypatch, capsys, argv):
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])
    try:
        rc = cli.main()
    except SystemExit as exc:
        rc = exc.code
    out = capsys.readouterr()
    return rc, out.out, out.err


def _envelope_file(tmp_path, name="questions.json", rows=None):
    rows = rows if rows is not None else [
        {"kind": "event", "question": "Widget 3 launch keynote", "date": LATER,
         "url": "https://widgets.example/keynote"},
        {"kind": "decision", "question": "Regulator rules on the Widget merger",
         "date": SOON, "url": "https://regulator.example/cases/widget"},
    ]
    path = tmp_path / name
    path.write_text(json.dumps({"schema": open_questions.SCHEMA, "items": rows}))
    return str(path)


def _block(rendered):
    envelope = rendered.split("<!-- EVIDENCE FOR SYNTHESIS", 1)[1].split(
        "<!-- END EVIDENCE FOR SYNTHESIS -->", 1
    )[0]
    assert rendered.count("## Open Questions") == 1
    body = envelope.split("## Open Questions", 1)[1].split("\n## ", 1)[0]
    return ("## Open Questions" + body).rstrip()


def test_a_mock_run_with_the_envelope_writes_the_topic_file_and_a_cached_render_keeps_the_ids(
    monkeypatch, capsys, tmp_path,
):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cli.env, "CONFIG_DIR", config_dir)
    rc, out, err = _run(
        monkeypatch, capsys, ["widget", *BASE, "--open-questions", _envelope_file(tmp_path)],
    )
    assert rc == 0, err
    block = _block(out)
    assert f"- Q1 [decision] {SOON}: Regulator rules on the Widget merger | https://regulator.example/cases/widget" in block
    assert f"- Q2 [event] {LATER}: Widget 3 launch keynote | https://widgets.example/keynote" in block

    stored = open_questions.load("Widget", 3600)
    assert [(e["id"], e["kind"], e["date"]) for e in stored] == [
        ("Q1", "decision", SOON), ("Q2", "event", LATER),
    ]
    assert len(list((config_dir / "open-questions").iterdir())) == 1

    cached = cli._load_last_report_cache("widget")
    assert cached is not None
    assert cached[0].artifacts["open_questions"]["entries"] == stored
    assert _block(render.render_compact(cached[0])) == block

    # A run on another topic replaces the report cache but not this topic's file.
    rc, _, _ = _run(monkeypatch, capsys, ["gadget", *BASE])
    assert rc == 0
    assert cli._load_last_report_cache("widget") is None
    assert open_questions.load("widget", 3600) == stored
    assert open_questions.load("gadget", 3600) == []


def test_a_run_with_no_markets_and_no_dated_items_prints_the_empty_line(monkeypatch, capsys):
    rc, out, _ = _run(monkeypatch, capsys, ["widget", *BASE])
    assert rc == 0
    assert _block(out).splitlines() == [
        "## Open Questions",
        "",
        "- No dated open questions were found for this topic.",
    ]


def test_a_comparison_prints_one_list_for_all_entities(monkeypatch, capsys, tmp_path):
    rc, out, err = _run(
        monkeypatch, capsys,
        ["widget vs gadget", *BASE, "--open-questions", _envelope_file(tmp_path)],
    )
    assert rc == 0, err
    assert "- Q1 [decision]" in _block(out) and "- Q2 [event]" in _block(out)


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"schema": "last30days-open-questions/0", "items": []}),
    json.dumps({"schema": open_questions.SCHEMA, "items": "none"}),
    json.dumps({"schema": open_questions.SCHEMA, "items": []}) + " " * open_questions.MAX_BYTES,
    None,
])
def test_an_oversized_or_malformed_envelope_exits_2_before_any_retrieval(
    monkeypatch, capsys, tmp_path, content,
):
    path = tmp_path / "questions.json"
    if content is not None:
        path.write_text(content)
    with mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("research ran")), \
         mock.patch.object(cli.env, "get_config", side_effect=AssertionError("config loaded")):
        rc, out, err = _run(monkeypatch, capsys, ["widget", *BASE, "--open-questions", str(path)])
    assert rc == 2 and not out
    assert f"--open-questions file {path}" in err
    assert "last30days-open-questions/1" in err


@pytest.mark.parametrize("argv", [
    ["widget", "--emit=json"],
    ["widget", "--emit=html"],
    ["widget", "--emit=context"],
    ["widget", "--hiring-signals"],
    ["--discover"],
    ["--drill", "3"],
    ["doctor"],
    ["queue", "list"],
    ["library", "search", "widget"],
    ["forecast", "scorecard"],
    ["--diagnose"],
    ["widget", "--diagnose"],
    ["widget", "--preflight"],
    ["widget", "--resolve-save-dir"],
    ["--welcome"],
    ["setup"],
])
def test_the_envelope_flag_is_never_a_silent_no_op(monkeypatch, capsys, tmp_path, argv):
    with mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("research ran")):
        rc, out, err = _run(monkeypatch, capsys, [
            *argv, "--open-questions", _envelope_file(tmp_path), "--mock", "--save-dir", "",
        ])
    assert rc == 2 and "--open-questions applies to topic research runs" in err and not out


def test_the_hosted_backend_refuses_the_envelope(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("LAST30DAYS_API_KEY", "test-key")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test")
    rc, out, err = _run(monkeypatch, capsys, [
        "widget", "--emit=compact", "--save-dir", "", "--no-browser-cookies",
        "--open-questions", _envelope_file(tmp_path),
    ])
    assert rc == 2 and not out
    assert "--open-questions is not supported by the hosted backend" in err


def test_hiring_signals_discovery_and_drill_runs_print_no_block(monkeypatch, capsys):
    rc, out, _ = _run(monkeypatch, capsys, ["widget", *BASE, "--hiring-signals"])
    assert rc == 0 and "## Open Questions" not in out
    rc, out, _ = _run(monkeypatch, capsys, ["--discover", *BASE])
    assert rc == 0 and "## Open Questions" not in out
    # The drilled report is the cached one, which carries a stored list.
    rc, out, _ = _run(monkeypatch, capsys, ["widget", *BASE])
    assert rc == 0 and "## Open Questions" in out
    rc, out, err = _run(monkeypatch, capsys, ["--drill", "1", *BASE])
    assert rc == 0, err
    assert "## Open Questions" not in out


def test_a_failure_building_the_list_never_fails_the_run(monkeypatch, capsys):
    with mock.patch.object(
        cli.pipeline, "gather_open_questions", side_effect=RuntimeError("boom"),
    ):
        rc, out, err = _run(monkeypatch, capsys, ["widget", *BASE])
    assert rc == 0 and "## Open Questions" not in out
    assert "could not build the open questions list: boom" in err
