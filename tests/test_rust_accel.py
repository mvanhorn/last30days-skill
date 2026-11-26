"""Differential parity and fallback tests for the Rust core accelerator."""

from __future__ import annotations

import os
import random
from pathlib import Path
from unittest import mock

import pytest

from lib import cluster, dedupe, library, library_index, rust_accel, schema, signals


def _has_rust_binary() -> bool:
    return rust_accel.find_binary() is not None


requires_rust = pytest.mark.skipif(
    not _has_rust_binary(),
    reason="last30days-core release binary not built",
)


def test_python_fallback_works_when_binary_missing(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_ENGINE", "auto")
    monkeypatch.setattr(rust_accel, "find_binary", lambda: None)

    assert rust_accel.is_available() is False
    items = [
        schema.SourceItem(
            item_id=f"id-{i}",
            source="reddit",
            title=f"Rust performance test item {i}",
            body="Benchmarking fallback behavior without any Rust toolchain.",
            url=f"https://example.com/{i}",
        )
        for i in range(15)
    ]
    kept = dedupe.dedupe_items(items, threshold=0.7)
    assert len(kept) >= 1


def test_forced_rust_mode_raises_when_binary_missing(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    monkeypatch.setattr(rust_accel, "find_binary", lambda: None)

    with pytest.raises(rust_accel.RustAccelError):
        rust_accel.dedupe_kept_indices([{"mode": "fuzzy", "text": "hello"}], 0.7)


@requires_rust
def test_dedupe_differential_parity(monkeypatch):
    rng = random.Random(1337)
    vocab = [
        "rust", "python", "tokio", "async", "claude", "codex", "benchmark",
        "latency", "memory", "sqlite", "markdown", "unicode", "café", "国产大模型",
        "测评", "github", "reddit", "youtube", "performance", "clippy",
    ]
    items: list[schema.SourceItem] = []
    for i in range(80):
        if i > 5 and i % 6 == 0:
            title = items[i - 4].title + " follow-up"
            body = items[i - 4].body
        else:
            title = " ".join(rng.sample(vocab, 6))
            body = " ".join(rng.sample(vocab, 14))
        source = "jobs" if i % 17 == 0 else ("polymarket" if i % 19 == 0 else "reddit")
        meta = {"matched_market": {"question": title}} if source == "polymarket" else {}
        items.append(
            schema.SourceItem(
                item_id=f"it-{i}",
                source=source,
                title=title,
                body=body,
                url=f"https://example.com/{source}/{i}",
                author=f"author_{i % 10}",
                container=f"r/sub{i % 5}",
                metadata=meta,
            )
        )

    for threshold in (0.3, 0.7, 0.95):
        monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
        py_kept = [it.item_id for it in dedupe.dedupe_items(items, threshold=threshold)]
        monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
        rs_kept = [it.item_id for it in dedupe.dedupe_items(items, threshold=threshold)]
        assert rs_kept == py_kept, f"mismatch at threshold={threshold}"


@requires_rust
def test_annotate_stream_differential_parity(monkeypatch):
    sources = [
        "reddit", "x", "youtube", "tiktok", "instagram", "hackernews",
        "github", "digg", "polymarket", "amazon", "jobs", "grounding",
    ]
    base_items: list[schema.SourceItem] = []
    for i, src in enumerate(sources * 3):
        eng: dict[str, object] = {}
        meta: dict[str, object] = {}
        if src == "reddit":
            eng = {"score": 10 * i, "num_comments": 3 * i, "upvote_ratio": 0.85}
            meta = {"top_comments": [{"score": i * 5}]}
        elif src in {"youtube", "tiktok", "instagram"}:
            eng = {"views": 5000 * (i + 1), "likes": 120 * i, "comments": 15 * i}
            meta = {"hashtags": ["claudecode", "rustlang"], "top_comments": [{"score": 25 * i}]}
        elif src == "github":
            eng = {"stars": 4000} if i % 2 == 0 else {"reactions": 40, "comments": 12}
            if i % 3 == 0:
                meta = {"labels": ["project-mode"]}
        elif src == "digg":
            eng = {"digg_score": 120 if i % 2 == 0 else 0, "postCount": 20, "uniqueAuthors": 15}
        elif src == "polymarket":
            eng = {"volume": 50000, "liquidity": 8000}
        elif src == "amazon":
            eng = {"ratings": 1200}
            meta = {"grounding_exempt": True}
        elif src == "hackernews":
            eng = {"points": 150, "comments": 65}
        elif src == "x":
            eng = {"likes": 90, "reposts": 12, "replies": 5, "quotes": 2}
        base_items.append(
            schema.SourceItem(
                item_id=f"{src}-{i}",
                source=src,
                title=f"Claude Code Rust rewrite benchmark {i}",
                body="Comparing JS and JavaScript tooling with AI agents in 2026.",
                snippet="Rust performance analysis.",
                url=f"https://example.com/{src}/{i}",
                published_at=f"2026-11-{(i % 25) + 1:02d}",
                engagement=eng,
                metadata=meta,
            )
        )

    for mode in ("strict_recent", "balanced_recent", "evergreen_ok"):
        py_items = [
            schema.SourceItem(**{**it.__dict__, "engagement": dict(it.engagement), "metadata": dict(it.metadata)})
            for it in base_items
        ]
        rs_items = [
            schema.SourceItem(**{**it.__dict__, "engagement": dict(it.engagement), "metadata": dict(it.metadata)})
            for it in base_items
        ]
        monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
        py_res = signals.annotate_stream(py_items, "Claude Code JS benchmark", mode, reference_date="2026-11-26")
        monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
        rs_res = signals.annotate_stream(rs_items, "Claude Code JS benchmark", mode, reference_date="2026-11-26")

        assert [x.item_id for x in rs_res] == [x.item_id for x in py_res], f"order mismatch ({mode})"
        for py_it, rs_it in zip(py_res, rs_res):
            assert rs_it.local_relevance == pytest.approx(py_it.local_relevance, abs=1e-9)
            assert rs_it.freshness == py_it.freshness
            assert rs_it.engagement_score == py_it.engagement_score
            assert rs_it.source_quality == pytest.approx(py_it.source_quality, abs=1e-9)
            assert rs_it.local_rank_score == pytest.approx(py_it.local_rank_score, abs=1e-9)


@requires_rust
def test_cluster_differential_parity(monkeypatch):
    def _make_cands():
        return [
            schema.Candidate(
                candidate_id=f"c{i}",
                item_id=f"c{i}",
                source=src,
                title=title,
                url=f"https://example.com/c{i}",
                snippet=snip,
                subquery_labels=["primary"],
                native_ranks={"primary": i + 1},
                local_relevance=0.8,
                freshness=80,
                engagement=50,
                source_quality=0.7,
                rrf_score=0.02,
                final_score=score,
                source_items=[
                    schema.SourceItem(
                        item_id=f"c{i}",
                        source=src,
                        title=title,
                        body=snip,
                        url=f"https://example.com/c{i}",
                        published_at=pub,
                        date_confidence="high",
                    )
                ],
                metadata={"range_from": "2026-10-27", "range_to": "2026-11-26"},
            )
            for i, (src, title, snip, score, pub) in enumerate(
                [
                    ("reddit", "Kanye West headlines Wireless Festival 2026", "Three nights in London.", 85.0, "2026-11-20"),
                    ("x", "BREAKING: Kanye West UK comeback at Wireless Festival", "July festival dates.", 80.0, "2026-11-21"),
                    ("polymarket", "Sam Altman equity stake in OpenAI 2026", "Will Sam Altman receive equity?", 78.0, "2026-11-22"),
                    ("reddit", "Sam Altman rivalry with Elon Musk escalates", "Feud continues over OpenAI.", 82.0, "2026-11-22"),
                    ("youtube", "Rust 1.97 compiler benchmark deep dive", "Release performance numbers.", 74.0, "2026-11-18"),
                    ("hackernews", "Rust 1.97 compiler benchmark results", "Release performance numbers discussed.", 72.0, "2026-11-19"),
                    ("reddit", "Rust 1.97 compiler benchmark archive", "Release performance numbers old post.", 99.0, "2025-06-01"),
                ]
            )
        ]

    plan = schema.QueryPlan(
        intent="breaking_news",
        freshness_mode="strict_recent",
        cluster_mode="story",
        raw_topic="tech news",
        subqueries=[],
        source_weights={},
    )
    monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
    py_clusters = cluster.cluster_candidates(_make_cands(), plan)
    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    rs_clusters = cluster.cluster_candidates(_make_cands(), plan)

    assert len(rs_clusters) == len(py_clusters)
    for py_cl, rs_cl in zip(py_clusters, rs_clusters):
        assert rs_cl.title == py_cl.title
        assert rs_cl.candidate_ids == py_cl.candidate_ids
        assert rs_cl.representative_ids == py_cl.representative_ids
        assert rs_cl.sources == py_cl.sources
        assert rs_cl.score == pytest.approx(py_cl.score, abs=1e-9)
        assert rs_cl.uncertainty == py_cl.uncertainty


@requires_rust
def test_library_sync_differential_parity(tmp_path: Path, monkeypatch):
    memory = tmp_path / "memory"
    memory.mkdir()
    briefs = tmp_path / "briefs"
    (memory / "public-raw.md").write_text(
        "# last30days v4.6.0: Rust Accelerator\n\n"
        "- Date range: 2026-10-27 to 2026-11-26\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Rust speeds up dedupe and markdown scanning (score 88, 2 items, sources: Reddit, HN)\n"
        "1. [reddit] Thread\n"
        "   - Evidence: Speedups exceeded 50x.\n",
        encoding="utf-8",
    )
    (memory / "mixed-private-raw.md").write_text(
        "# last30days v4.6.0: Mixed Report\n\n"
        "- Date range: 2026-10-27 to 2026-11-26\n\n"
        f"{library.PRIVATE_CORPUS_START}\n## From your files\nTop secret note.\n{library.PRIVATE_CORPUS_END}\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Public cluster title (score 70, 1 item, sources: Reddit)\n"
        "1. [reddit] Public thread\n"
        "   - Evidence: Public evidence.\n",
        encoding="utf-8",
    )

    py_db = tmp_path / "py.db"
    rs_db = tmp_path / "rs.db"

    monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
    py_sync = library_index.sync_library(memory, briefs, db_path=py_db)
    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    rs_sync = library_index.sync_library(memory, briefs, db_path=rs_db)

    assert (rs_sync.indexed, rs_sync.removed, rs_sync.unchanged) == (
        py_sync.indexed,
        py_sync.removed,
        py_sync.unchanged,
    )
    # Second sync in Rust must report all unchanged (fingerprint parity!).
    rs_sync_2 = library_index.sync_library(memory, briefs, db_path=rs_db)
    assert rs_sync_2.indexed == 0
    assert rs_sync_2.unchanged == py_sync.indexed

    # Cross-sync: Rust reading the DB populated by Python must ALSO see 0 changes!
    rs_on_py_db = library_index.sync_library(memory, briefs, db_path=py_db)
    assert rs_on_py_db.indexed == 0
    assert rs_on_py_db.removed == 0
    assert rs_on_py_db.unchanged == py_sync.indexed
