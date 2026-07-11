"""Cross-topic library FTS search and passive self-citation tests."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
import store
from lib import library, library_index, pipeline, render, schema


def _write_report(
    directory: Path,
    *,
    name: str,
    topic: str,
    date: str,
    headline: str,
    evidence: str,
) -> Path:
    path = directory / name
    path.write_text(
        f"""# last30days v3.11.1: {topic}

- Date range: 2026-06-10 to {date}

## Ranked Evidence Clusters

### 1. {headline} (score 42, 2 items, sources: Reddit)
1. [reddit] A useful thread
   - URL: https://example.com/{name}
   - Evidence: {evidence}
""",
        encoding="utf-8",
    )
    return path


def test_index_search_relevance_and_incremental_edit_delete_rename(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    briefs = tmp_path / "briefs"
    db_path = tmp_path / "library.db"
    mcp = _write_report(
        memory,
        name="openclaw-raw.md",
        topic="OpenClaw",
        date="2026-07-01",
        headline="MCP servers need permission boundaries",
        evidence="MCP servers should isolate tools and credentials.",
    )
    unrelated = _write_report(
        memory,
        name="video-raw.md",
        topic="Product video",
        date="2026-07-02",
        headline="Captions improve completion",
        evidence="Short captions help viewers follow demos.",
    )

    first = library_index.sync_library(memory, briefs, db_path=db_path)
    matches = library_index.search(
        "MCP servers",
        db_path=db_path,
        store_db_path=tmp_path / "missing-store.db",
    )

    assert first.indexed == 2
    assert [match.topic for match in matches] == ["OpenClaw"]
    assert matches[0].source_kind == "brief"
    assert library_index.sync_library(memory, briefs, db_path=db_path).unchanged == 2

    mcp.write_text(
        mcp.read_text(encoding="utf-8").replace(
            "MCP servers should isolate tools and credentials.",
            "MCP servers need gateway security and credential isolation.",
        ),
        encoding="utf-8",
    )
    edited = library_index.sync_library(memory, briefs, db_path=db_path)
    assert edited.indexed == 1
    assert library_index.search(
        "gateway security",
        db_path=db_path,
        store_db_path=tmp_path / "missing-store.db",
    )[0].topic == "OpenClaw"

    renamed = memory / "openclaw-raw-client.md"
    mcp.rename(renamed)
    unrelated.unlink()
    pruned = library_index.sync_library(memory, briefs, db_path=db_path)
    assert pruned.indexed == 1
    assert pruned.removed == 2
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM library_documents").fetchone()[0] == 1


def test_search_merges_dated_store_sightings(tmp_path, monkeypatch):
    store_db = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", store_db)
    store.init_db()
    topic = store.add_topic("AI agents")
    run_id = store.record_run(topic["id"], status="completed")
    store.store_findings(
        run_id,
        topic["id"],
        [
            {
                "source": "reddit",
                "source_url": "https://reddit.com/r/agents/1",
                "source_title": "MCP server security checklist",
                "content": "Operators are adopting MCP servers with strict permission boundaries.",
                "summary": "MCP permissions became a deployment concern.",
                "engagement_score": 2100,
                "relevance_score": 0.9,
            }
        ],
    )
    with sqlite3.connect(store_db) as conn:
        conn.execute(
            "UPDATE research_runs SET run_date = '2026-06-14 12:00:00' WHERE id = ?",
            (run_id,),
        )
        conn.commit()

    matches = library_index.search(
        "MCP servers",
        db_path=tmp_path / "missing-library.db",
        store_db_path=store_db,
    )

    assert len(matches) == 1
    assert matches[0].topic == "AI agents"
    assert matches[0].published_date.isoformat() == "2026-06-14"
    assert matches[0].engagement == 2100
    assert "2.1K engagement" in render.render_library_search("MCP servers", matches)


def test_corrupt_index_is_rebuilt_from_scanned_library(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_report(
        memory,
        name="mcp-raw.md",
        topic="MCP",
        date="2026-07-03",
        headline="MCP servers get searchable",
        evidence="Library search finds MCP servers offline.",
    )
    db_path = tmp_path / "library.db"
    db_path.write_bytes(b"not a sqlite database")

    result = library_index.sync_library(memory, tmp_path / "briefs", db_path=db_path)

    assert result.rebuilt is True
    assert library_index.search(
        "MCP servers", db_path=db_path, store_db_path=tmp_path / "none.db"
    )


def test_fts5_capability_failure_has_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(library_index, "fts5_available", lambda: False)

    with pytest.raises(library_index.LibrarySearchUnavailable, match="FTS5"):
        library_index.sync_library(tmp_path / "memory", tmp_path / "briefs", db_path=tmp_path / "db")


def test_library_search_cli_reuses_library_word_dispatch(tmp_path, monkeypatch, capsys):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_report(
        memory,
        name="openclaw-raw.md",
        topic="OpenClaw",
        date="2026-07-01",
        headline="MCP servers need permission boundaries",
        evidence="MCP servers should isolate tools and credentials.",
    )
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "briefs")
    monkeypatch.setattr(library_index, "DEFAULT_LIBRARY_DB", tmp_path / "library.db")
    monkeypatch.setattr(library_index, "DEFAULT_STORE_DB", tmp_path / "research.db")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "search", "MCP", "servers", "--save-dir", str(memory)],
    )

    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "# Library search: MCP servers" in output
    assert "## OpenClaw - 2026-07-01" in output


def test_markdown_save_incrementally_syncs_the_shared_library_index(tmp_path):
    report = mock.Mock(topic="MCP servers")
    with mock.patch.object(render, "render_full", return_value="# saved\n"), mock.patch.object(
        library_index, "sync_library"
    ) as sync:
        saved = cli.save_output(report, "md", str(tmp_path))

    assert saved.is_file()
    sync.assert_called_once_with(tmp_path.resolve())


def test_self_citation_overlap_nonoverlap_and_escape_hatch(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_report(
        memory,
        name="openclaw-raw.md",
        topic="OpenClaw",
        date="2026-07-01",
        headline="MCP servers need permission boundaries",
        evidence="MCP servers should isolate tools and credentials.",
    )
    config = {
        "LAST30DAYS_LIBRARY_CONTEXT": "on",
        "LAST30DAYS_MEMORY_DIR": str(memory),
        "_LAST30DAYS_LIBRARY_BRIEFS_DIR": str(tmp_path / "briefs"),
        "_LAST30DAYS_LIBRARY_DB": str(tmp_path / "library.db"),
        "_LAST30DAYS_STORE_DB": str(tmp_path / "research.db"),
    }

    context, warning = pipeline._load_library_context(
        topic="MCP servers",
        config=config,
        mock=False,
        internal_subrun=False,
        x_handle=None,
        github_user=None,
        github_repos=None,
    )
    missing, _ = pipeline._load_library_context(
        topic="underwater basket weaving",
        config=config,
        mock=False,
        internal_subrun=False,
        x_handle=None,
        github_user=None,
        github_repos=None,
    )

    assert warning is None
    assert [(item.topic, item.published_date) for item in context] == [("OpenClaw", "2026-07-01")]
    assert missing == []

    with mock.patch.object(library_index, "sync_library") as sync:
        disabled, disabled_warning = pipeline._load_library_context(
            topic="MCP servers",
            config={"LAST30DAYS_LIBRARY_CONTEXT": "off"},
            mock=False,
            internal_subrun=False,
            x_handle=None,
            github_user=None,
            github_repos=None,
        )
    assert disabled == []
    assert disabled_warning is None
    sync.assert_not_called()


def test_report_renders_from_your_library_section():
    report = schema.Report(
        topic="MCP servers",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock",
            rerank_model="mock",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="recent",
            cluster_mode="topic",
            raw_topic="MCP servers",
            subqueries=[],
            source_weights={},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={},
        errors_by_source={},
        library_context=[
            schema.LibraryContext(
                topic="OpenClaw",
                published_date="2026-07-01",
                headline="MCP servers need permission boundaries",
                summary="Teams isolated tools and credentials.",
                source_kind="brief",
            )
        ],
    )

    rendered = render.render_compact(report)

    assert "## From your library" in rendered
    assert "You researched **OpenClaw** on 2026-07-01" in rendered
    assert schema.to_dict(report)["library_context"][0]["topic"] == "OpenClaw"
    assert schema.report_from_dict(schema.to_dict(report)).library_context == report.library_context
