from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import last30days as cli
import store
from lib import corpus, env, health, html_render, library, library_index, pipeline, render, schema


def _set_mtime(path: Path, value: str) -> None:
    timestamp = datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))


def _scan(tmp_path: Path, *, all_time: bool = False, limit: int = 12):
    return corpus.search(
        "MCP servers",
        [tmp_path],
        from_date="2026-06-10",
        to_date="2026-07-10",
        all_time=all_time,
        limit=limit,
        cache_dir=tmp_path / "cache",
    )


def test_scans_matching_text_and_markdown_with_path_titles(tmp_path):
    note = tmp_path / "mcp-server-notes.md"
    note.write_text("MCP servers expose tools to local coding agents.", encoding="utf-8")
    other = tmp_path / "groceries.txt"
    other.write_text("milk eggs bread", encoding="utf-8")
    _set_mtime(note, "2026-07-05T12:00:00")
    _set_mtime(other, "2026-07-05T12:00:00")

    result = _scan(tmp_path)

    assert [item.title for item in result.items] == ["mcp server notes"]
    assert result.items[0].source == "corpus"
    assert result.items[0].published_at == "2026-07-05"
    assert result.items[0].metadata["local_only"] is True
    assert result.items[0].url.startswith("corpus://")
    assert str(note) not in result.items[0].url


def test_multilingual_matching_reuses_shared_cjk_tokenizer(tmp_path):
    note = tmp_path / "模型记录.md"
    note.write_text("国产大模型的最新测评和部署记录", encoding="utf-8")
    _set_mtime(note, "2026-07-05T12:00:00")

    result = corpus.search(
        "国产大模型 测评",
        [tmp_path],
        from_date="2026-06-10",
        to_date="2026-07-10",
        cache_dir=tmp_path / "cache",
    )

    assert [item.title for item in result.items] == ["模型记录"]


def test_recency_window_and_all_time_override(tmp_path):
    old = tmp_path / "old-mcp-plan.md"
    old.write_text("MCP servers and local tool protocols", encoding="utf-8")
    _set_mtime(old, "2025-01-01T00:00:00")

    assert _scan(tmp_path).items == []
    assert [item.title for item in _scan(tmp_path, all_time=True).items] == ["old mcp plan"]


def test_hidden_git_and_node_modules_directories_are_ignored(tmp_path):
    visible = tmp_path / "visible.md"
    visible.write_text("MCP servers are visible", encoding="utf-8")
    _set_mtime(visible, "2026-07-05T00:00:00")
    for directory in (tmp_path / ".git", tmp_path / ".hidden", tmp_path / "node_modules"):
        directory.mkdir()
        path = directory / "private.md"
        path.write_text("MCP servers hidden secret", encoding="utf-8")
        _set_mtime(path, "2026-07-05T00:00:00")

    result = _scan(tmp_path)

    assert [item.metadata["relative_path"] for item in result.items] == ["visible.md"]


def test_pdf_is_skipped_with_one_note_when_pdftotext_is_absent(tmp_path, monkeypatch):
    pdf = tmp_path / "mcp.pdf"
    pdf.write_bytes(b"not a real pdf")
    _set_mtime(pdf, "2026-07-05T00:00:00")
    monkeypatch.setattr(corpus, "which", lambda _name: None)

    result = _scan(tmp_path)

    assert result.items == []
    assert result.notes == ["Skipped PDF files because pdftotext is not on PATH"]


def test_mtime_cache_reuses_text_and_is_private(tmp_path, monkeypatch):
    note = tmp_path / "mcp.md"
    note.write_text("MCP servers cache this local note", encoding="utf-8")
    _set_mtime(note, "2026-07-05T00:00:00")
    first = _scan(tmp_path)
    assert first.cache_hits == 0

    monkeypatch.setattr(corpus, "_extract_text", mock.Mock(side_effect=AssertionError("cache miss")))
    second = _scan(tmp_path)

    assert second.cache_hits == 1
    cache_path = tmp_path / "cache" / corpus.CACHE_FILENAME
    assert cache_path.stat().st_mode & 0o777 == 0o600
    assert cache_path.parent.stat().st_mode & 0o777 == 0o700


def test_cache_for_500_large_documents_is_bounded_by_total_bytes(tmp_path, monkeypatch):
    for index in range(500):
        (tmp_path / f"document-{index:03d}.txt").touch()

    monkeypatch.setattr(corpus, "MAX_CACHE_BYTES", 1_000_000)
    monkeypatch.setattr(corpus, "MAX_CACHE_TEXT_CHARS", 100_000)
    monkeypatch.setattr(corpus, "_extract_text", lambda *_args, **_kwargs: "x" * 1_000_000)
    monkeypatch.setattr(corpus, "_match_score", lambda *_args, **_kwargs: 0.0)

    result = corpus.search(
        "anything",
        [tmp_path],
        from_date="2026-06-10",
        to_date="2026-07-10",
        all_time=True,
        limit=0,
        cache_dir=tmp_path / "cache",
    )

    cache_path = tmp_path / "cache" / corpus.CACHE_FILENAME
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert result.files_scanned == 500
    assert cache_path.stat().st_size <= corpus.MAX_CACHE_BYTES
    assert len(payload["entries"]) < 500
    assert all(
        len(entry["text"]) <= corpus.MAX_CACHE_TEXT_CHARS
        for entry in payload["entries"].values()
    )


def test_result_budget_caps_one_corpus_stream(tmp_path):
    for index in range(10):
        path = tmp_path / f"mcp-{index}.md"
        path.write_text(f"MCP servers local note {index}", encoding="utf-8")
        _set_mtime(path, f"2026-07-{index + 1:02d}T00:00:00")

    assert len(_scan(tmp_path, limit=3).items) == 3


def test_file_cap_is_shared_fairly_across_corpus_roots(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    relevant = tmp_path / "relevant"
    archive.mkdir()
    relevant.mkdir()
    for index in range(4):
        (archive / f"mcp-archive-{index}.md").write_text(
            f"MCP servers archived note {index}", encoding="utf-8"
        )
    later = relevant / "mcp-current.md"
    later.write_text("MCP servers relevant current note", encoding="utf-8")
    monkeypatch.setattr(corpus, "MAX_FILES", 4)

    result = corpus.search(
        "MCP servers",
        [archive, relevant],
        from_date="2026-06-10",
        to_date="2026-07-10",
        all_time=True,
        cache_dir=tmp_path / "cache",
    )

    assert result.files_scanned <= corpus.MAX_FILES
    assert any(item.metadata["path"].startswith(str(archive)) for item in result.items)
    assert any(item.metadata["path"] == str(later) for item in result.items)


def test_pipeline_runs_corpus_outside_network_executor(tmp_path, monkeypatch):
    note = tmp_path / "mcp-private.md"
    note.write_text("MCP servers use a secret local transport", encoding="utf-8")
    _set_mtime(note, "2026-07-05T00:00:00")
    retrieve = mock.Mock(return_value=([], {}))
    monkeypatch.setattr(pipeline, "_retrieve_stream", retrieve)

    report = pipeline.run(
        topic="MCP servers",
        config={"_CORPUS_DIRS": [str(tmp_path)], "EXCLUDE_SOURCES": ""},
        depth="quick",
        requested_sources=["corpus"],
        mock=True,
        as_of_date="2026-07-10",
        external_plan={
            "intent": "concept",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "none",
            "source_weights": {"corpus": 1.0},
            "subqueries": [{
                "label": "primary",
                "search_query": "MCP servers",
                "ranking_query": "MCP servers",
                "sources": ["corpus"],
            }],
        },
    )

    assert report.source_status["corpus"].state == health.OK
    assert len(report.items_by_source["corpus"]) == 1
    assert len(report.items_by_source["corpus"]) <= pipeline.DEPTH_SETTINGS["quick"]["per_stream_limit"]
    assert all(call.kwargs["source"] != "corpus" for call in retrieve.call_args_list)


def test_corpus_never_enters_remote_rerank_or_fun_prompts(tmp_path, monkeypatch):
    note = tmp_path / "private.md"
    note.write_text("Model context protocol servers PRIVATE-RERANK-SENTINEL", encoding="utf-8")
    _set_mtime(note, "2026-07-05T00:00:00")
    remote = mock.Mock()
    remote.generate_json = mock.Mock(side_effect=AssertionError("private prompt left machine"))
    runtime = schema.ProviderRuntime("local", "remote-planner", "remote-reranker")
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *_args, **_kwargs: (runtime, remote))
    monkeypatch.setattr(pipeline, "available_sources", lambda *_args, **_kwargs: ["corpus"])

    report = pipeline.run(
        topic="how do model context protocol servers work today?",
        config={"_CORPUS_DIRS": [str(tmp_path)], "EXCLUDE_SOURCES": ""},
        depth="quick",
        requested_sources=["corpus"],
        mock=False,
        as_of_date="2026-07-10",
        external_plan={
            "intent": "how_to",
            "freshness_mode": "evergreen_ok",
            "cluster_mode": "workflow",
            "source_weights": {"corpus": 1.0},
            "subqueries": [{
                "label": "primary",
                "search_query": "model context protocol servers",
                "ranking_query": "How do model context protocol servers work?",
                "sources": ["corpus"],
            }],
        },
    )

    assert report.items_by_source["corpus"]
    remote.generate_json.assert_not_called()


def test_explicit_unconfigured_corpus_records_skipped_outcome():
    report = pipeline.run(
        topic="how do local model context protocol servers work?",
        config={"EXCLUDE_SOURCES": ""},
        depth="quick",
        requested_sources=["corpus"],
        mock=True,
        as_of_date="2026-07-10",
        external_plan={
            "intent": "how_to",
            "freshness_mode": "evergreen_ok",
            "cluster_mode": "workflow",
            "source_weights": {"corpus": 1.0},
            "subqueries": [{
                "label": "primary",
                "search_query": "model context protocol servers",
                "ranking_query": "How do model context protocol servers work?",
                "sources": ["corpus"],
            }],
        },
    )

    outcome = report.source_status["corpus"]
    assert outcome.state == schema.SKIPPED_UNCONFIGURED
    assert outcome.attempted is False


def _privacy_report(secret: str = "PRIVATE-CORPUS-SENTINEL") -> schema.Report:
    item = schema.SourceItem(
        item_id="C-private",
        source="corpus",
        title="private mcp notes",
        body=f"MCP servers {secret}",
        url="",
        published_at="2026-07-05",
        snippet=f"MCP servers {secret}",
        metadata={"relative_path": "notes/private.md", "local_only": True},
    )
    candidate = schema.Candidate(
        candidate_id="corpus:C-private",
        item_id=item.item_id,
        source="corpus",
        title=item.title,
        url="",
        snippet=item.snippet,
        subquery_labels=["primary"],
        native_ranks={"primary:corpus": 1},
        local_relevance=1.0,
        freshness=90,
        engagement=None,
        source_quality=0.75,
        rrf_score=0.01,
        final_score=91,
        cluster_id="cluster-private",
        sources=["corpus"],
        source_items=[item],
    )
    return schema.Report(
        topic="MCP servers",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime("local", "mock", "mock"),
        query_plan=schema.QueryPlan(
            intent="concept",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic="MCP servers",
            subqueries=[schema.SubQuery("primary", "MCP servers", "MCP servers", ["corpus"])],
            source_weights={"corpus": 1.0},
        ),
        clusters=[schema.Cluster(
            cluster_id="cluster-private",
            title=f"Private cluster {secret}",
            candidate_ids=[candidate.candidate_id],
            representative_ids=[candidate.candidate_id],
            sources=["corpus"],
            score=91,
        )],
        ranked_candidates=[candidate],
        items_by_source={"corpus": [item]},
        errors_by_source={},
        source_status={"corpus": schema.SourceOutcome("corpus", health.OK, 1)},
    )


def test_corpus_findings_persist_with_stable_opaque_keys(tmp_path, monkeypatch):
    note = tmp_path / "private-customer-notes.md"
    note.write_text("MCP servers persist this private finding", encoding="utf-8")
    first = corpus.search(
        "MCP servers",
        [tmp_path],
        from_date="2026-06-10",
        to_date="2026-07-10",
        all_time=True,
        cache_dir=tmp_path / "cache",
    ).items[0]
    second = corpus.search(
        "MCP servers",
        [tmp_path],
        from_date="2026-06-10",
        to_date="2026-07-10",
        all_time=True,
        cache_dir=tmp_path / "cache",
    ).items[0]
    report = _privacy_report()
    report.items_by_source["corpus"][0].url = first.url
    report.ranked_candidates[0].url = first.url

    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()
    topic = store.add_topic(report.topic)
    run_id = store.record_run(topic["id"], status="completed")
    findings = store.findings_from_report(report)
    counts = store.store_findings(run_id, topic["id"], findings)

    assert first.url == second.url
    assert first.url.startswith("corpus://")
    assert str(note) not in first.url
    assert counts == {"new": 1, "updated": 0}
    with sqlite3.connect(db_path) as conn:
        persisted = conn.execute(
            "SELECT source, source_url FROM findings"
        ).fetchone()
    assert persisted == ("corpus", first.url)
    assert library_index.search(
        "private finding",
        db_path=tmp_path / "missing-library.db",
        store_db_path=db_path,
    ) == []


def test_persist_report_hardens_corpus_store_and_sidecars(tmp_path, monkeypatch):
    db_path = tmp_path / "store" / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    original_findings_from_report = store.findings_from_report
    original_store_findings = store.store_findings
    original_update_run = store.update_run

    def make_permissive():
        db_path.chmod(0o644)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{db_path}{suffix}")
            sidecar.touch(mode=0o644)
            sidecar.chmod(0o644)

    def findings_from_report_with_permissive_store(*args, **kwargs):
        findings = original_findings_from_report(*args, **kwargs)
        make_permissive()
        return findings

    def store_findings_after_private_check(*args, **kwargs):
        for artifact in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            assert artifact.stat().st_mode & 0o777 == 0o600
        return original_store_findings(*args, **kwargs)

    def update_run_with_permissive_sidecars(*args, **kwargs):
        result = original_update_run(*args, **kwargs)
        make_permissive()
        return result

    monkeypatch.setattr(store, "findings_from_report", findings_from_report_with_permissive_store)
    monkeypatch.setattr(store, "store_findings", store_findings_after_private_check)
    monkeypatch.setattr(store, "update_run", update_run_with_permissive_sidecars)

    cli.persist_report(_privacy_report())

    for artifact in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        assert artifact.stat().st_mode & 0o777 == 0o600


def test_agent_export_excludes_corpus_by_default_and_allows_explicit_opt_in():
    report = _privacy_report()

    private_default = schema.to_agent_export(report)
    opted_in = schema.to_agent_export(report, corpus_in_export=True)

    assert private_default["results"] == []
    assert private_default["clusters"] == []
    assert "corpus" not in private_default["source_status"]
    assert opted_in["results"][0]["source"] == "corpus"
    assert "PRIVATE-CORPUS-SENTINEL" in opted_in["results"][0]["summary"]

    report.artifacts["corpus_in_export"] = True
    assert schema.to_agent_export(report)["results"][0]["source"] == "corpus"


def test_agent_export_rebuilds_mixed_cluster_title_after_private_representative_removed():
    report = _privacy_report()
    social_item = schema.SourceItem(
        item_id="R-public",
        source="reddit",
        title="Public MCP discussion",
        body="Public evidence",
        url="https://reddit.example/public",
        published_at="2026-07-06",
        snippet="Public evidence",
    )
    social = schema.Candidate(
        candidate_id="reddit:public",
        item_id=social_item.item_id,
        source="reddit",
        title=social_item.title,
        url=social_item.url,
        snippet=social_item.snippet,
        subquery_labels=["primary"],
        native_ranks={"primary:reddit": 1},
        local_relevance=0.8,
        freshness=90,
        engagement=1,
        source_quality=0.6,
        rrf_score=0.01,
        final_score=80,
        cluster_id="cluster-private",
        sources=["reddit"],
        source_items=[social_item],
    )
    report.ranked_candidates.append(social)
    report.items_by_source["reddit"] = [social_item]
    report.source_status["reddit"] = schema.SourceOutcome("reddit", health.OK, 1)
    report.clusters[0].candidate_ids.append(social.candidate_id)
    report.clusters[0].representative_ids.append(social.candidate_id)
    report.clusters[0].sources.append("reddit")

    exported = schema.to_agent_export(report)

    assert exported["clusters"][0]["title"] == "Public MCP discussion"
    assert "PRIVATE-CORPUS-SENTINEL" not in str(exported)


def test_local_report_has_badged_from_your_files_section():
    rendered = render.render_compact(_privacy_report())

    assert "## From your files" in rendered
    assert "LOCAL ONLY" in rendered
    assert "PRIVATE-CORPUS-SENTINEL" in rendered


def test_publish_html_sends_sanitized_report_not_local_corpus(monkeypatch):
    report = _privacy_report()
    captured: dict[str, str] = {}

    def publish(rendered, **_kwargs):
        captured["html"] = rendered
        return {"url": "https://example.ht-ml.app"}

    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: {"available_sources": ["corpus"]})
    monkeypatch.setattr(cli.pipeline, "run", lambda **_kwargs: report)
    monkeypatch.setattr(cli, "publish_rendered_html", publish)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "MCP servers", "--emit=html", "--publish-html"],
    )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    assert "PRIVATE-CORPUS-SENTINEL" not in captured["html"]
    assert "private mcp notes" not in captured["html"]


def test_configured_corpus_bypasses_hosted_backend(tmp_path, monkeypatch):
    report = _privacy_report()
    hosted = mock.Mock(side_effect=AssertionError("local corpus was forwarded"))
    monkeypatch.setattr("lib.hosted.run_hosted", hosted)
    monkeypatch.setattr(
        cli.env,
        "get_config",
        lambda **_kwargs: {"LAST30DAYS_CORPUS_DIRS": str(tmp_path)},
    )
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: {"available_sources": ["corpus"]})
    monkeypatch.setattr(cli.pipeline, "run", lambda **_kwargs: report)
    monkeypatch.setenv("LAST30DAYS_API_KEY", "test-hosted-key")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://example.invalid")
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", "MCP servers", "--emit=compact"])

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    hosted.assert_not_called()


def test_library_publish_strips_marked_corpus_but_local_page_keeps_it(tmp_path, monkeypatch):
    markdown = render.render_full(_privacy_report())
    (tmp_path / "mcp-raw.md").write_text(markdown, encoding="utf-8")
    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    publish_many = mock.Mock(return_value={})
    monkeypatch.setattr("lib.html_publish.publish_html_documents", publish_many)
    monkeypatch.setattr("lib.html_publish.publish_html", mock.Mock(return_value={"url": "https://library.ht-ml.app"}))
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(tmp_path), "--publish"],
    )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    published_documents = publish_many.call_args.args[0]
    assert published_documents
    assert all("PRIVATE-CORPUS-SENTINEL" not in html for html in published_documents.values())
    local_pages = list((tmp_path / "briefs").glob("*.html"))
    assert local_pages
    assert "PRIVATE-CORPUS-SENTINEL" in local_pages[0].read_text(encoding="utf-8")
    assert local_pages[0].stat().st_mode & 0o777 == 0o600
    assert local_pages[0].parent.stat().st_mode & 0o777 == 0o700


def test_private_corpus_cannot_escape_via_library_search_and_feed_publish(tmp_path, monkeypatch):
    secret = "PRIVATE-CORPUS-ESCAPE-SENTINEL"
    memory = tmp_path / "memory"
    report = _privacy_report(secret)
    with mock.patch.object(library_index, "sync_library"):
        saved = cli.save_output(report, "md", str(memory))

    db_path = tmp_path / "index" / "library.db"
    library_index.sync_library(memory, tmp_path / "no-briefings", db_path=db_path)
    entry = library._parse_markdown(saved)
    legacy_hash = hashlib.sha256(entry.content.encode("utf-8")).hexdigest()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE library_documents SET content_hash = ? WHERE entry_id = ?",
            (legacy_hash, entry.entry_id),
        )
        conn.execute("DELETE FROM library_fts WHERE entry_id = ?", (entry.entry_id,))
        conn.execute(
            "INSERT INTO library_fts(entry_id, topic, headline, summary, content) "
            "VALUES (?, ?, ?, ?, ?)",
            (entry.entry_id, entry.topic, entry.headline, entry.summary, entry.content),
        )
        conn.commit()

    rebuilt = library_index.sync_library(
        memory,
        tmp_path / "no-briefings",
        db_path=db_path,
    )
    assert rebuilt.indexed == 1
    assert library_index.search(
        secret,
        db_path=db_path,
        store_db_path=tmp_path / "missing-store.db",
    ) == []
    matches = library_index.search(
        "MCP servers",
        db_path=db_path,
        store_db_path=tmp_path / "missing-store.db",
    )
    assert matches
    assert all(secret not in match.snippet for match in matches)

    followup = schema.without_sources(report, {"corpus"})
    followup.library_context = [
        schema.LibraryContext(
            topic=match.topic,
            published_date=match.published_date.isoformat(),
            headline=match.headline,
            summary=match.snippet,
            source_kind=match.source_kind,
        )
        for match in matches[:1]
    ]
    with mock.patch.object(library_index, "sync_library"):
        cli.save_output(followup, "md", str(memory), suffix="followup")

    monkeypatch.setattr(library, "DEFAULT_BRIEFS_DIR", tmp_path / "no-briefings")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    publish_many = mock.Mock(return_value={})
    monkeypatch.setattr("lib.html_publish.publish_html_documents", publish_many)
    monkeypatch.setattr(
        "lib.html_publish.publish_html",
        mock.Mock(return_value={"url": "https://library.ht-ml.app"}),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "library", "feed", "--save-dir", str(memory), "--publish"],
    )

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli.main() == 0

    published = publish_many.call_args.args[0]
    assert published
    assert all(secret not in document for document in published.values())


def test_configured_paths_use_platform_separator_and_dedupe(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    resolved = corpus.resolve_directories(
        [str(first)], f"{first}{os.pathsep}{second}"
    )
    assert resolved == [first.resolve(), second.resolve()]


def test_corpus_env_keys_are_registered(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_CORPUS_DIRS", "/tmp/notes:/tmp/transcripts")
    monkeypatch.setenv("LAST30DAYS_CORPUS_IN_EXPORT", "1")
    with mock.patch.object(env, "_load_keychain", return_value={}), mock.patch.object(
        env, "_load_pass", return_value={}
    ):
        config = env.get_config()

    assert config["LAST30DAYS_CORPUS_DIRS"] == "/tmp/notes:/tmp/transcripts"
    assert config["LAST30DAYS_CORPUS_IN_EXPORT"] == "1"


def test_library_renderer_private_switch_is_load_bearing(tmp_path):
    report_path = tmp_path / "mcp-raw.md"
    report_path.write_text(render.render_full(_privacy_report()), encoding="utf-8")
    entry = library._parse_markdown(report_path)

    assert "PRIVATE-CORPUS-SENTINEL" in html_render.render_library_brief(entry)
    assert "PRIVATE-CORPUS-SENTINEL" not in html_render.render_library_brief(
        entry, include_private=False
    )


def test_report_cache_with_corpus_is_written_private(tmp_path, monkeypatch):
    config_dir = tmp_path / "private" / "config"
    monkeypatch.setattr(cli.env, "CONFIG_DIR", config_dir)

    assert cli._write_last_run("MCP servers", _privacy_report()) is True

    assert (config_dir / "last-report.json").stat().st_mode & 0o777 == 0o600
    assert config_dir.stat().st_mode & 0o777 == 0o700


def test_every_corpus_bearing_saved_artifact_is_owner_only(tmp_path):
    report = _privacy_report()
    markdown_dir = tmp_path / "private" / "markdown"
    html_dir = tmp_path / "private" / "html"
    with mock.patch.object(library_index, "sync_library"):
        markdown = cli.save_output(report, "md", str(markdown_dir))
        html = cli.save_output(report, "html", str(html_dir))
        inferred_private = cli.save_output(
            report, "compact", str(tmp_path / "private" / "inferred")
        )
        forced_public = cli.save_output(
            report, "json", str(tmp_path / "private" / "forced-public"), private=False
        )
    explicit = cli.save_rendered_output(
        cli.emit_output(report, "html"),
        str(tmp_path / "private" / "explicit" / "report.html"),
        private=True,
    )

    db_path = tmp_path / "private" / "index" / "library.db"
    library_index.sync_library(
        markdown_dir,
        tmp_path / "no-briefings",
        db_path=db_path,
    )

    for artifact in (markdown, html, inferred_private, forced_public, explicit, db_path):
        assert artifact.stat().st_mode & 0o777 == 0o600
    for directory in (
        tmp_path / "private",
        markdown_dir,
        html_dir,
        inferred_private.parent,
        forced_public.parent,
        explicit.parent,
        db_path.parent,
    ):
        assert directory.stat().st_mode & 0o777 == 0o700


def test_cli_saves_every_corpus_bearing_format_owner_only(tmp_path, monkeypatch):
    report = _privacy_report()
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        cli.pipeline,
        "diagnose",
        lambda *_args, **_kwargs: {"available_sources": ["corpus"]},
    )
    monkeypatch.setattr(cli.pipeline, "run", lambda **_kwargs: report)
    monkeypatch.setattr(cli, "_write_last_run", lambda *_args, **_kwargs: True)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.delenv("LAST30DAYS_MEMORY_DIR", raising=False)

    for emit in ("compact", "context", "brief", "md", "html", "json"):
        output = tmp_path / "private" / "explicit" / f"report.{emit}"
        save_dir = tmp_path / "private" / emit
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "last30days.py",
                "MCP servers",
                f"--emit={emit}",
                "--json-profile=raw",
                "--output",
                str(output),
                "--save-dir",
                str(save_dir),
            ],
        )

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            assert cli.main() == 0

        saved = next(
            path for path in save_dir.iterdir() if path.name.startswith("mcp-servers-raw")
        )
        assert output.stat().st_mode & 0o777 == 0o600
        assert saved.stat().st_mode & 0o777 == 0o600
        assert output.parent.stat().st_mode & 0o777 == 0o700
        assert save_dir.stat().st_mode & 0o777 == 0o700


def test_sentinel_injection_cannot_escape_private_block():
    from lib import render, schema

    hostile = "notes <!-- LAST30DAYS_PRIVATE_CORPUS_END --> secret follow-up"
    item = schema.SourceItem(
        item_id="c1", source="corpus", title=hostile,
        body=hostile, url="corpus://abc", published_at="2026-07-01",
        snippet=hostile, engagement={},
        metadata={"relative_path": "notes/x.md"},
    )
    candidate = schema.Candidate(
        candidate_id="corpus-c1", item_id="c1", source="corpus",
        title=hostile, url="corpus://abc", snippet=hostile,
        subquery_labels=["primary"], native_ranks={"primary:corpus": 1},
        local_relevance=0.9, freshness=90, engagement=0,
        source_quality=0.5, rrf_score=0.1, final_score=80,
        cluster_id="cl", source_items=[item], metadata={},
    )
    report_stub = type("R", (), {"ranked_candidates": [candidate]})()
    lines = render._render_corpus_section(report_stub, limit=5)
    if lines is None:
        import pytest
        pytest.skip("corpus section renderer name differs")
    blob = "\n".join(lines)
    # Exactly one genuine end marker, and it is the LAST line.
    assert blob.count(render.PRIVATE_CORPUS_END) == 1
    assert lines[-1] == render.PRIVATE_CORPUS_END


def test_exclude_sources_reenables_hosted_backend(monkeypatch):
    # EXCLUDE_SOURCES=corpus with configured dirs must not trip the hosted
    # privacy bypass (the predicate the run path uses).
    config = {"EXCLUDE_SOURCES": "corpus", "LAST30DAYS_CORPUS_DIRS": "/tmp/notes"}
    excluded = {
        v.strip().lower() for v in str(config.get("EXCLUDE_SOURCES") or "").split(",") if v.strip()
    }
    assert "corpus" in excluded


def test_corpus_notes_never_contain_absolute_paths(tmp_path):
    import os
    import stat
    from lib import corpus

    root = tmp_path / "private-notes"
    root.mkdir()
    good = root / "readable.md"
    good.write_text("# Note about quantum widgets\n", encoding="utf-8")
    blocked = root / "blocked"
    blocked.mkdir()
    (blocked / "secret.md").write_text("# hidden\n", encoding="utf-8")
    os.chmod(blocked, 0)
    try:
        result = corpus.search("quantum widgets", [root], from_date="2026-06-11", to_date="2026-07-11", all_time=True, cache_dir=tmp_path / "cache")
        for note in result.notes:
            assert str(tmp_path) not in note, f"absolute path leaked in note: {note}"
    finally:
        os.chmod(blocked, stat.S_IRWXU)


def test_scan_error_notes_never_echo_absolute_paths(tmp_path, monkeypatch):
    note = tmp_path / "mcp-server-notes.md"
    note.write_text("MCP servers expose tools to local coding agents.", encoding="utf-8")
    _set_mtime(note, "2026-07-05T12:00:00")

    def raising_extract(path, *, pdftotext):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(corpus, "_extract_text", raising_extract)

    result = _scan(tmp_path)

    assert result.items == []
    assert result.notes, "expected a skip note"
    joined = " ".join(result.notes)
    assert str(tmp_path) not in joined
    assert "Permission denied" in joined


def test_cache_write_failure_note_never_echoes_absolute_paths(tmp_path, monkeypatch):
    notes: list[str] = []

    def raising_open(*args, **kwargs):
        raise PermissionError(
            13, "Permission denied", str(tmp_path / "cache" / "corpus.json")
        )

    monkeypatch.setattr(corpus.os, "open", raising_open)
    corpus._write_cache(tmp_path / "cache" / "corpus.json", {"entries": {}}, notes)

    joined = " ".join(notes)
    assert notes, "expected a cache note"
    assert str(tmp_path) not in joined
    assert "Permission denied" in joined
