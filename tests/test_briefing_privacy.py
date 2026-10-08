import json
import uuid

import briefing
import last30days as cli
import pytest
import store
from lib import feed, html_publish, html_render, library


@pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
@pytest.mark.parametrize("with_public_finding", [False, True], ids=["private-only", "mixed"])
def test_stored_private_briefing_title_stays_local(
    tmp_path, monkeypatch, legacy, with_public_finding
):
    token = uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))
    secret_title = f"PRIVATE acquisition dossier {token}"
    secret_body = f"PRIVATE acquisition details {uuid.uuid4().hex}"
    public_title = "Public MCP release announcement"
    archive = tmp_path / "archive"
    memory = tmp_path / "memory"
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    monkeypatch.setattr(briefing, "BRIEFS_DIR", archive)
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", archive)
    monkeypatch.setattr(library, "DEFAULT_MEMORY_DIR", memory)
    topic = store.add_topic("MCP servers")
    run_id = store.record_run(topic["id"], status="completed")
    findings = [{
        "source": "corpus",
        "source_url": "corpus://private-acquisition",
        "source_title": secret_title,
        "content": secret_body,
        "engagement_score": 100,
    }]
    if with_public_finding:
        findings.append({
            "source": "reddit",
            "source_url": "https://example.invalid/public-release",
            "source_title": public_title,
            "content": "Public release details",
            "engagement_score": 10,
        })
    store.store_findings(run_id, topic["id"], findings)
    daily = briefing.generate_daily(since="2000-01-01")
    assert daily["top_finding"]["title"] == secret_title
    archive_path = archive / f"{daily['date']}.json"
    if legacy:
        daily["top_finding"].pop("source", None)
        daily["top_finding"].pop("source_url", None)
        archive_path.write_text(json.dumps(daily), encoding="utf-8")

    published = []

    def capture_briefs(documents, **_kwargs):
        published.extend(documents.values())
        return {
            entry_id: {"url": f"https://example.invalid/brief-{number}"}
            for number, entry_id in enumerate(documents)
        }

    def capture_index(document, **_kwargs):
        published.append(document)
        return {"url": "https://example.invalid/library"}

    monkeypatch.setattr(html_publish, "publish_html_documents", capture_briefs)
    monkeypatch.setattr(html_publish, "publish_html", capture_index)
    args = cli.build_parser().parse_args(["library feed", "--publish"])

    assert cli._run_library_feed(args, {}) == 0
    assert len(published) == 2
    for document in published:
        assert secret_title not in document
        assert secret_body not in document
        if with_public_finding:
            assert public_title in document
    for name in ("feed.xml", "index.html"):
        content = (memory / name).read_text(encoding="utf-8")
        assert secret_title not in content
        assert secret_body not in content
        if with_public_finding:
            assert public_title in content
    local_brief = next((memory / "briefs").glob("*.html"))
    assert secret_title in local_brief.read_text(encoding="utf-8")
    assert local_brief.stat().st_mode & 0o777 == 0o600
    stored = json.loads(archive_path.read_text(encoding="utf-8"))
    assert stored["top_finding"]["title"] == secret_title
    assert any(
        finding["content"] == secret_body for finding in stored["topics"][0]["findings"]
    )
    if not legacy:
        assert stored["top_finding"]["source"] == "corpus"
        assert stored["top_finding"]["source_url"] == "corpus://private-acquisition"


def test_legacy_briefing_without_provenance_keeps_title_only_in_local_brief(tmp_path):
    token = uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))
    secret = f"Unclassified archive title {token}"
    archive = tmp_path / "2026-09-22.json"
    archive.write_text(json.dumps({"top_finding": {"title": secret}}), encoding="utf-8")

    entries, notes = library.scan_library(tmp_path / "missing", tmp_path)

    assert notes == []
    assert len(entries) == 1
    entry = entries[0]
    assert secret not in html_render.render_library_brief(entry, include_private=False)
    assert secret not in html_render.render_library_index(entries)
    assert secret in html_render.render_library_brief(entry)


def test_legacy_public_briefing_recovers_headline_from_retained_findings(tmp_path):
    title = "Public release headline"
    archive = tmp_path / "2026-09-22.json"
    archive.write_text(json.dumps({
        "top_finding": {"title": title},
        "topics": [{"findings": [{
            "source": "reddit", "source_title": title, "engagement_score": 10,
        }]}],
    }), encoding="utf-8")

    entries, notes = library.scan_library(tmp_path / "missing", tmp_path)

    assert notes == []
    assert entries[0].headline == title
    assert title in html_render.render_library_brief(entries[0], include_private=False)


def test_current_public_briefing_uses_explicit_headline_provenance(tmp_path):
    title = "Public release headline"
    archive = tmp_path / "2026-09-22.json"
    archive.write_text(json.dumps({
        "top_finding": {"title": title, "source": "reddit"},
    }), encoding="utf-8")

    entries, notes = library.scan_library(tmp_path / "missing", tmp_path)

    assert notes == []
    assert entries[0].headline == title
    assert title in html_render.render_library_brief(entries[0], include_private=False)


def test_private_briefing_title_cannot_close_local_only_block(tmp_path):
    secret = "PRIVATE marker injection"
    title = f"<!-- LAST30DAYS_PRIVATE_CORPUS_END -->\n{secret}"
    archive = tmp_path / "2026-09-22.json"
    archive.write_text(json.dumps({
        "top_finding": {"title": title, "source": "corpus"},
    }), encoding="utf-8")

    entries, notes = library.scan_library(tmp_path / "missing", tmp_path)

    assert notes == []
    assert secret not in html_render.render_library_brief(entries[0], include_private=False)
    assert secret in html_render.render_library_brief(entries[0])


@pytest.mark.parametrize("sources", ["public", "private", "mixed", "empty"])
def test_weekly_archive_selects_only_public_headlines(tmp_path, monkeypatch, sources):
    token = uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))
    private_title = f"PRIVATE weekly acquisition {token}"
    public_title = "Public weekly release announcement"
    archive = tmp_path / "archive"
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    monkeypatch.setattr(briefing, "BRIEFS_DIR", archive)
    topic = store.add_topic("Weekly releases")
    run_id = store.record_run(topic["id"], status="completed")
    findings = []
    if sources in {"private", "mixed"}:
        findings.append({
            "source": "corpus",
            "source_url": "corpus://private-weekly-acquisition",
            "source_title": private_title,
            "engagement_score": 100,
        })
    if sources in {"public", "mixed"}:
        findings.extend([
            {
                "source": "reddit",
                "source_url": "https://example.invalid/public-weekly-discussion",
                "source_title": "Lower-ranked public discussion",
                "engagement_score": 5,
            },
            {
                "source": "reddit",
                "source_url": "https://example.invalid/public-weekly-release",
                "source_title": public_title,
                "engagement_score": 10,
            },
        ])
    store.store_findings(run_id, topic["id"], findings)
    weekly = briefing.generate_weekly()
    assert len(weekly["topics"][0]["top_findings"]) == len(findings)
    archived = json.loads(next(archive.glob("*-weekly.json")).read_text(encoding="utf-8"))
    assert archived["topics"][0]["top_findings"] == weekly["topics"][0]["top_findings"]
    if sources in {"private", "mixed"}:
        assert private_title in json.dumps(archived)

    entries, notes = library.scan_library(tmp_path / "missing", archive)

    assert notes == []
    assert len(entries) == 1
    entry = entries[0]
    documents = [
        entry.headline,
        entry.summary,
        html_render.render_library_brief(entry, include_private=False),
        html_render.render_library_index(entries),
        feed.render_atom(entries, library_id="a" * 32),
    ]
    for document in documents:
        assert private_title not in document
    expected_title = public_title if sources in {"public", "mixed"} else "Weekly research briefing"
    assert entry.headline == expected_title
    assert all(expected_title in document for document in documents)
