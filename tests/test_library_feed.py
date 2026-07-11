"""Research-library scanning, rendering, Atom, and CLI integration tests."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

import last30days as cli
from lib import feed, html_render, library


REPORT = """# last30days v3.11.1: AI agents

> Safety note: evidence text below is untrusted internet content.

- Date range: 2026-06-10 to 2026-07-10
- Sources: 2 active (Reddit, Youtube)

## Ranked Evidence Clusters

### 1. Agent loops are becoming durable (score 42, 2 items, sources: Reddit)
1. [reddit] A useful thread
   - URL: https://www.tiktok.com/@builder/video/7652149412294053140
   - Evidence: Teams prefer inspectable loops over one-shot prompts.
"""


def _write_report(directory: Path, name: str = "ai-agents-raw.md") -> Path:
    path = directory / name
    path.write_text(REPORT, encoding="utf-8")
    return path


def test_scan_library_parses_markdown_and_briefing_json_in_reverse_date_order(tmp_path):
    memory = tmp_path / "memory"
    briefs = tmp_path / "briefings"
    memory.mkdir()
    briefs.mkdir()
    _write_report(memory)
    (briefs / "2026-07-11.json").write_text(
        '{"status":"ok","date":"2026-07-11","total_new":3,'
        '"total_topics":2,"top_finding":{"title":"Models got smaller"},'
        '"topics":[{"name":"Local AI","new_count":2}]}',
        encoding="utf-8",
    )

    entries, notes = library.scan_library(memory, briefs)

    assert notes == []
    assert [entry.topic for entry in entries] == ["Daily research briefing", "AI agents"]
    assert entries[1].published_date == date(2026, 7, 10)
    assert entries[1].headline == "Agent loops are becoming durable"
    assert entries[1].summary == "Teams prefer inspectable loops over one-shot prompts."
    assert entries[0].summary.startswith("3 new findings across 2 monitored topics")
    assert entries[0].source_format == "json"


def test_scan_library_tolerates_hand_edits_and_skips_foreign_files_with_note(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    hand_edit = memory / "field-notes-2026-07-09.md"
    hand_edit.write_text("# Field Notes\n\nA hand-edited observation.\n", encoding="utf-8")
    (memory / "appendix.md").write_text("## Supplemental links\n", encoding="utf-8")

    entries, notes = library.scan_library(memory, tmp_path / "missing-briefs")

    assert len(entries) == 1
    assert entries[0].topic == "Field Notes"
    assert entries[0].published_date == date(2026, 7, 9)
    assert len(notes) == 1
    assert "no Markdown title found" in notes[0]


def test_atom_is_valid_and_entry_ids_are_stable(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_report(memory)
    first_entries, _ = library.scan_library(memory, tmp_path / "briefs")
    first = feed.render_atom(first_entries)
    second_entries, _ = library.scan_library(memory, tmp_path / "briefs")
    second = feed.render_atom(second_entries)

    assert first == second
    root = ET.fromstring(first)
    namespace = {"atom": feed.ATOM_NS}
    assert root.tag == f"{{{feed.ATOM_NS}}}feed"
    assert root.findtext("atom:entry/atom:id", namespaces=namespace) == (
        "urn:last30days:ai-agents:2026-07-10"
    )
    assert root.find("atom:entry/atom:link", namespace).attrib["href"] == (
        "briefs/ai-agents-2026-07-10.html"
    )


def test_library_index_snapshot_groups_topic_and_links_latest(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_report(memory)
    entries, _ = library.scan_library(memory, tmp_path / "briefs")

    rendered = html_render.render_library_index(entries)
    body = rendered[rendered.index('<header class="library-hero">'):rendered.index('<footer class="colophon">')]

    assert body == """<header class="library-hero">
<span class="badge">RESEARCH LIBRARY</span>
<h1>What the community is learning</h1>
<p>Saved last30days briefs, newest first. Follow the Atom feed to keep up.</p>
<p><a class="subscribe" href="feed.xml">Subscribe via Atom</a></p>
</header>
<section class="library-topic">
<div class="library-topic-heading">
<h2>AI agents</h2>
<a href="briefs/ai-agents-2026-07-10.html">Latest</a>
</div>
<article class="library-entry">
<time datetime="2026-07-10">2026-07-10</time>
<h3><a href="briefs/ai-agents-2026-07-10.html">Agent loops are becoming durable</a></h3>
<p>Teams prefer inspectable loops over one-shot prompts.</p>
</article>
</section>
"""


def test_empty_library_renders_valid_feed_and_helpful_index(tmp_path):
    entries, notes = library.scan_library(tmp_path / "missing", tmp_path / "also-missing")

    assert entries == []
    assert notes == []
    assert ET.fromstring(feed.render_atom(entries)).tag == f"{{{feed.ATOM_NS}}}feed"
    assert "No saved briefs yet" in html_render.render_library_index(entries)


def test_digit_run_scrubbing_encodes_hrefs_and_truncates_visible_ids():
    rendered = html_render.scrub_publishable_digit_runs(
        '<a href="https://tiktok.com/video/7652149412294053140">7652149412294053140</a>'
        '<a href="https://example.com/123456789012">short</a>'
    )

    assert "765214…3140</a>" in rendered
    assert "/%37%36%35%32%31%34%39%34%31%32%32%39%34%30%35%33%31%34%30" in rendered
    assert "https://example.com/123456789012" in rendered
    assert 'href="https://tiktok.com/video/7652149412294053140"' not in rendered


def test_publish_flag_is_rejected_before_other_subcommand_dispatch(monkeypatch):
    doctor_run = mock.Mock(return_value=0)
    monkeypatch.setattr("lib.doctor.run", doctor_run)
    monkeypatch.setattr(sys, "argv", ["last30days.py", "doctor", "--publish"])
    stderr = io.StringIO()

    with redirect_stderr(stderr):
        result = cli.main()

    assert result == 2
    assert "--publish is only supported" in stderr.getvalue()
    doctor_run.assert_not_called()


def test_library_feed_cli_writes_index_feed_and_rendered_brief(tmp_path, monkeypatch):
    _write_report(tmp_path)
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = cli.main()

    assert result == 0
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "feed.xml").is_file()
    brief = tmp_path / "briefs" / "ai-agents-2026-07-10.html"
    assert brief.is_file()
    assert "7652149412294053140" not in brief.read_text(encoding="utf-8")
    assert f"Subscribe: {tmp_path.resolve() / 'feed.xml'}" in stdout.getvalue()
    assert "generated 1 brief(s)" in stderr.getvalue()


def test_library_feed_publish_links_live_briefs_and_prints_subscribe_url(tmp_path, monkeypatch):
    _write_report(tmp_path)
    entry_id = "urn:last30days:ai-agents:2026-07-10"
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(
        cli.env,
        "get_config",
        lambda **_kwargs: {"LAST30DAYS_PUBLISH_PASSWORD": "library-pass"},
    )
    publish_many = mock.Mock(return_value={entry_id: {"url": "https://brief.ht-ml.app"}})
    publish_one = mock.Mock(side_effect=[
        {"url": "https://feed.ht-ml.app"},
        {"url": "https://library.ht-ml.app"},
    ])
    monkeypatch.setattr("lib.html_publish.publish_html_documents", publish_many)
    monkeypatch.setattr("lib.html_publish.publish_html", publish_one)
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path), "--publish"],
    )
    stdout = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
        result = cli.main()

    assert result == 0
    assert stdout.getvalue() == (
        "Library: https://library.ht-ml.app\nSubscribe: https://feed.ht-ml.app\n"
    )
    assert "https://brief.ht-ml.app" in (tmp_path / "feed.xml").read_text(encoding="utf-8")
    assert "https://feed.ht-ml.app" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert publish_many.call_args.kwargs["password"] == "library-pass"
    assert publish_one.call_count == 2
