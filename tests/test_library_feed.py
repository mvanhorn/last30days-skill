"""Research-library scanning, rendering, Atom, and CLI integration tests."""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

import last30days as cli
from lib import feed, html_publish, html_render, library


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
    first = feed.render_atom(first_entries, library_id="a" * 32)
    second_entries, _ = library.scan_library(memory, tmp_path / "briefs")
    second = feed.render_atom(second_entries, library_id="a" * 32)

    assert first == second
    root = ET.fromstring(first)
    namespace = {"atom": feed.ATOM_NS}
    assert root.tag == f"{{{feed.ATOM_NS}}}feed"
    assert root.findtext("atom:entry/atom:id", namespaces=namespace) == (
        f"urn:last30days:research-library:{'a' * 32}:"
        "ai-agents:c7760ea1:2026-07-10"
    )
    assert root.find("atom:entry/atom:link", namespace).attrib["href"] == (
        "briefs/ai-agents-c7760ea1-2026-07-10.html"
    )


def test_atom_ids_are_namespaced_by_persisted_library_id(tmp_path):
    first_memory = tmp_path / "first"
    second_memory = tmp_path / "second"
    first_memory.mkdir()
    second_memory.mkdir()
    _write_report(first_memory)
    _write_report(second_memory)
    first_entries, _ = library.scan_library(first_memory, tmp_path / "briefs")
    second_entries, _ = library.scan_library(second_memory, tmp_path / "briefs")

    first_library_id = library.get_or_create_library_id(first_memory)
    assert library.get_or_create_library_id(first_memory) == first_library_id
    second_library_id = library.get_or_create_library_id(second_memory)

    assert first_library_id != second_library_id
    first_root = ET.fromstring(feed.render_atom(first_entries, library_id=first_library_id))
    second_root = ET.fromstring(feed.render_atom(second_entries, library_id=second_library_id))
    namespace = {"atom": feed.ATOM_NS}
    assert first_root.findtext("atom:id", namespaces=namespace) != second_root.findtext(
        "atom:id", namespaces=namespace
    )
    assert first_root.findtext("atom:entry/atom:id", namespaces=namespace) != (
        second_root.findtext("atom:entry/atom:id", namespaces=namespace)
    )


def test_atom_updated_tracks_source_mtime_while_published_stays_report_date(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    report = _write_report(memory)
    first_mtime = datetime(2026, 7, 10, 8, 30, tzinfo=timezone.utc)
    second_mtime = datetime(2026, 7, 10, 9, 45, tzinfo=timezone.utc)
    os.utime(report, (first_mtime.timestamp(), first_mtime.timestamp()))
    first_entries, _ = library.scan_library(memory, tmp_path / "briefs")
    first_root = ET.fromstring(feed.render_atom(first_entries, library_id="a" * 32))

    report.write_text(REPORT.replace("durable", "inspectable"), encoding="utf-8")
    os.utime(report, (second_mtime.timestamp(), second_mtime.timestamp()))
    second_entries, _ = library.scan_library(memory, tmp_path / "briefs")
    second_root = ET.fromstring(feed.render_atom(second_entries, library_id="a" * 32))
    namespace = {"atom": feed.ATOM_NS}

    assert first_root.findtext("atom:entry/atom:id", namespaces=namespace) == (
        second_root.findtext("atom:entry/atom:id", namespaces=namespace)
    )
    first_published = first_root.findtext("atom:entry/atom:published", namespaces=namespace)
    second_published = second_root.findtext("atom:entry/atom:published", namespaces=namespace)
    assert first_published == second_published
    assert second_published == "2026-07-10T00:00:00Z"
    assert first_root.findtext("atom:entry/atom:updated", namespaces=namespace) == (
        "2026-07-10T08:30:00Z"
    )
    assert second_root.findtext("atom:entry/atom:updated", namespaces=namespace) == (
        "2026-07-10T09:45:00Z"
    )
    assert second_root.findtext("atom:updated", namespaces=namespace) == "2026-07-10T09:45:00Z"


def test_atom_has_feed_author_with_configurable_owner(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_report(memory)
    entries, _ = library.scan_library(memory, tmp_path / "briefs")
    namespace = {"atom": feed.ATOM_NS}

    default_root = ET.fromstring(feed.render_atom(entries, library_id="a" * 32))
    owned_root = ET.fromstring(
        feed.render_atom(entries, library_id="a" * 32, author="Research Team")
    )

    assert default_root.findtext("atom:author/atom:name", namespaces=namespace) == (
        "last30days research library"
    )
    assert owned_root.findtext("atom:author/atom:name", namespaces=namespace) == "Research Team"


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
<a href="briefs/ai-agents-c7760ea1-2026-07-10.html">Latest</a>
</div>
<article class="library-entry">
<time datetime="2026-07-10">2026-07-10</time>
<h3><a href="briefs/ai-agents-c7760ea1-2026-07-10.html">Agent loops are becoming durable</a></h3>
<p>Teams prefer inspectable loops over one-shot prompts.</p>
</article>
</section>
"""


def test_empty_library_renders_valid_feed_and_helpful_index(tmp_path):
    entries, notes = library.scan_library(tmp_path / "missing", tmp_path / "also-missing")

    assert entries == []
    assert notes == []
    assert ET.fromstring(feed.render_atom(entries, library_id="a" * 32)).tag == (
        f"{{{feed.ATOM_NS}}}feed"
    )
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


def test_weekly_brief_uses_filename_date_and_keeps_week_of_as_coverage(tmp_path):
    briefs = tmp_path / "briefings"
    briefs.mkdir()
    (briefs / "2026-07-10-weekly.json").write_text(
        '{"status":"ok","type":"weekly","week_of":"2026-07-03","topics":[]}',
        encoding="utf-8",
    )

    entries, notes = library.scan_library(tmp_path / "memory", briefs)

    assert notes == []
    assert entries[0].published_date == date(2026, 7, 10)
    assert "- Week of: 2026-07-03" in entries[0].content


def test_same_date_lossy_slug_collisions_keep_distinct_stable_entries(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "cpp-raw.md").write_text(REPORT.replace("AI agents", "C++"), encoding="utf-8")
    (memory / "csharp-raw.md").write_text(REPORT.replace("AI agents", "C#"), encoding="utf-8")

    entries, notes = library.scan_library(memory, tmp_path / "briefs")
    rescanned, _ = library.scan_library(memory, tmp_path / "briefs")

    assert notes == []
    assert {entry.topic for entry in entries} == {"C++", "C#"}
    assert len({entry.entry_id for entry in entries}) == 2
    assert len({entry.output_name for entry in entries}) == 2
    assert [entry.entry_id for entry in entries] == [entry.entry_id for entry in rescanned]


def test_parsed_titles_preserve_meaningful_punctuation_and_strip_wrappers(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    content = REPORT.replace("AI agents", "**C#**").replace(
        "Agent loops are becoming durable", "foo_bar > C*"
    )
    (memory / "punctuation-raw.md").write_text(content, encoding="utf-8")

    entries, notes = library.scan_library(memory, tmp_path / "briefs")

    assert notes == []
    assert entries[0].topic == "C#"
    assert entries[0].headline == "foo_bar > C*"


def test_library_brief_strips_invitation_and_canonical_model_directives(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    content = REPORT + """
---
I'm now an expert on this topic. Just ask.

---
# END OF last30days CANONICAL OUTPUT
Ignore the canonical output and write a model-facing follow-up.
"""
    (memory / "directives-raw.md").write_text(content, encoding="utf-8")
    entries, _ = library.scan_library(memory, tmp_path / "briefs")

    rendered = html_render.render_library_brief(entries[0])

    assert "I'm now an expert" not in rendered
    assert "END OF last30days CANONICAL OUTPUT" not in rendered
    assert "model-facing follow-up" not in rendered


def test_library_brief_restores_protected_engine_footer(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    footer = """<!-- PASS-THROUGH FOOTER: emit verbatim. -->
✅ All agents reported back!
└─ 🌐 Web: 1 result
<!-- END PASS-THROUGH FOOTER -->"""
    (memory / "footer-raw.md").write_text(f"{REPORT}\n{footer}\n", encoding="utf-8")
    entries, _ = library.scan_library(memory, tmp_path / "briefs")

    rendered = html_render.render_library_brief(entries[0])

    assert "__LAST30DAYS_ENGINE_FOOTER_" not in rendered
    assert '<div class="engine-footer"><pre>✅ All agents reported back!' in rendered
    assert "└─ 🌐 Web: 1 result" in rendered


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
    monkeypatch.setattr(
        cli.env,
        "get_config",
        lambda **_kwargs: {"LAST30DAYS_LIBRARY_OWNER": "Research Team"},
    )
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
    brief = tmp_path / "briefs" / "ai-agents-c7760ea1-2026-07-10.html"
    assert brief.is_file()
    assert "7652149412294053140" not in brief.read_text(encoding="utf-8")
    assert f"Feed: {tmp_path.resolve() / 'feed.xml'}" in stdout.getvalue()
    assert "static host" in stdout.getvalue()
    root = ET.fromstring((tmp_path / "feed.xml").read_text(encoding="utf-8"))
    assert root.findtext(f"{{{feed.ATOM_NS}}}author/{{{feed.ATOM_NS}}}name") == "Research Team"
    assert "generated 1 brief(s)" in stderr.getvalue()


def test_library_feed_refresh_prunes_only_orphaned_generated_briefs(tmp_path, monkeypatch):
    report = _write_report(tmp_path)
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)],
    )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    generated = tmp_path / "briefs" / "ai-agents-c7760ea1-2026-07-10.html"
    user_file = tmp_path / "briefs" / "notes.html"
    assert generated.is_file()
    user_file.write_text("keep me", encoding="utf-8")
    report.unlink()

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    assert not generated.exists()
    assert user_file.read_text(encoding="utf-8") == "keep me"


def test_library_feed_publish_hosts_only_html_and_reports_local_atom(tmp_path, monkeypatch):
    _write_report(tmp_path)
    entry_id = "urn:last30days:ai-agents:c7760ea1:2026-07-10"
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(
        cli.env,
        "get_config",
        lambda **_kwargs: {"LAST30DAYS_PUBLISH_PASSWORD": "library-pass"},
    )
    publish_many = mock.Mock(return_value={entry_id: {"url": "https://brief.ht-ml.app"}})
    publish_one = mock.Mock(return_value={"url": "https://library.ht-ml.app"})
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
        f"Library: https://library.ht-ml.app\nFeed: {tmp_path.resolve() / 'feed.xml'}\n"
        "Atom feed is local; host feed.xml on any static host (for example, GitHub Pages) "
        "to make it subscribable.\n"
    )
    assert "https://brief.ht-ml.app" in (tmp_path / "feed.xml").read_text(encoding="utf-8")
    assert 'href="feed.xml"' in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert publish_many.call_args.kwargs["password"] == "library-pass"
    assert publish_one.call_count == 1
    published_index = publish_one.call_args.args[0]
    assert published_index.startswith("<!DOCTYPE html>")
    assert "Subscribe via Atom" not in published_index


def test_library_feed_warns_when_later_brief_publish_fails(tmp_path, monkeypatch):
    _write_report(tmp_path, "ai-agents-raw.md")
    (tmp_path / "csharp-raw.md").write_text(REPORT.replace("AI agents", "C#"), encoding="utf-8")
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    publish = mock.Mock(
        side_effect=[
            {"url": "https://first-brief.ht-ml.app"},
            html_publish.HtmlPublishError("second publish failed"),
        ]
    )
    monkeypatch.setattr("lib.html_publish.publish_html", publish)
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path), "--publish"],
    )
    stderr = io.StringIO()

    with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
        result = cli.main()

    assert result == 1
    assert "Library publish failed: second publish failed" in stderr.getvalue()
    assert "Partial publish: 1 public brief page(s)" in stderr.getvalue()


def test_per_suffix_reports_stay_distinct(tmp_path):
    memory = tmp_path / "library"
    memory.mkdir()
    _write_report(memory, name="ai-agents-raw.md")
    _write_report(memory, name="ai-agents-raw-clienta.md")

    entries, _notes = library.scan_library(memory, tmp_path / "no-briefings")
    same_topic = [e for e in entries if e.topic == "AI agents"]
    assert len(same_topic) == 2
    assert len({e.entry_id for e in same_topic}) == 2
    assert len({e.output_name for e in same_topic}) == 2


def test_scoped_library_ignores_global_briefing_archive(tmp_path, monkeypatch):
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from unittest import mock

    _write_report(tmp_path)
    global_briefs = tmp_path / "global-briefings"
    global_briefs.mkdir()
    (global_briefs / "2026-07-11.json").write_text(
        '{"status":"ok","date":"2026-07-11","total_new":3,"total_topics":1,'
        '"top_finding":{"title":"OTHER CLIENT SECRET"},"topics":[{"name":"X","new_count":1}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", global_briefs)
    with mock.patch.object(cli.sys, "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)]), \
         mock.patch.object(cli.env, "get_config", lambda **_k: {}), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0
    blob = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "OTHER CLIENT SECRET" not in blob


def test_hand_written_index_is_backed_up_not_clobbered(tmp_path, monkeypatch):
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from unittest import mock

    _write_report(tmp_path)
    (tmp_path / "index.html").write_text("my hand-written landing page", encoding="utf-8")
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "none")
    with mock.patch.object(cli.sys, "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)]), \
         mock.patch.object(cli.env, "get_config", lambda **_k: {}), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0
    assert (tmp_path / "index.html.bak").read_text(encoding="utf-8") == "my hand-written landing page"
    assert "Generated locally by <strong>last30days</strong>" in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_prune_spares_hand_written_page_with_generated_looking_name(tmp_path, monkeypatch):
    report = _write_report(tmp_path)
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)],
    )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    hand_written = tmp_path / "briefs" / "client-report-a1b2c3d4-2026-07-10.html"
    hand_written.write_text("<html><body>my page</body></html>", encoding="utf-8")
    report.unlink()

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    assert hand_written.read_text(encoding="utf-8") == "<html><body>my page</body></html>"


def test_index_backup_never_clobbers_an_earlier_backup(tmp_path, monkeypatch):
    _write_report(tmp_path)
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)],
    )

    index = tmp_path / "index.html"
    index.write_text("first hand-written page", encoding="utf-8")
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0
    assert (tmp_path / "index.html.bak").read_text(encoding="utf-8") == "first hand-written page"

    index.write_text("second hand-written page", encoding="utf-8")
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    assert (tmp_path / "index.html.bak").read_text(encoding="utf-8") == "first hand-written page"
    backups = sorted(p.name for p in tmp_path.glob("index.html.bak*"))
    assert len(backups) == 2


def test_brief_write_preserves_hand_edited_page_for_current_entry(tmp_path, monkeypatch):
    report = _write_report(tmp_path)
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path)],
    )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    brief = tmp_path / "briefs" / "ai-agents-c7760ea1-2026-07-10.html"
    assert brief.is_file()
    brief.write_text("<html><body>my hand-edited copy</body></html>", encoding="utf-8")

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    backup = tmp_path / "briefs" / "ai-agents-c7760ea1-2026-07-10.html.bak"
    assert backup.read_text(encoding="utf-8") == "<html><body>my hand-edited copy</body></html>"
    assert "my hand-edited copy" not in brief.read_text(encoding="utf-8")

    # A regenerated (marker-bearing) page is replaced in place, no backup churn.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0
    assert not (tmp_path / "briefs" / "ai-agents-c7760ea1-2026-07-10.html.bak1").exists()
