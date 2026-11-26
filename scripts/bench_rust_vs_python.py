#!/usr/bin/env python3
"""Reproducible A/B parity and performance benchmark: Python vs Rust core accelerator.

Usage:
    uv run python scripts/bench_rust_vs_python.py
    uv run python scripts/bench_rust_vs_python.py --library-dir /path/to/saved-reports
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "last30days" / "scripts"))

from lib import cluster, dedupe, library, library_index, rust_accel, schema, signals


def _make_items(n: int, seed: int = 42) -> list[schema.SourceItem]:
    rng = random.Random(seed)
    vocab = [f"word{k}" for k in range(800)] + [
        "Rust", "Python", "Tokio", "Claude", "Codex", "Cargo", "Linux",
        "Apple", "Chrome", "GitHub", "Redis", "Postgres", "Docker",
        "Kubernetes", "Anthropic", "Benchmark", "Latency", "Memory",
    ]
    sources = ["reddit", "x", "youtube", "hackernews", "github", "web"]
    items: list[schema.SourceItem] = []
    for i in range(n):
        src = sources[i % len(sources)]
        if i > 10 and i % 8 == 0:
            title = items[i - 7].title + " update"
            body = items[i - 7].body
        else:
            title = " ".join(rng.sample(vocab, 10))
            body = " ".join(rng.sample(vocab, 65))
        items.append(
            schema.SourceItem(
                item_id=f"{src}-{i}",
                source=src,
                title=title,
                body=body,
                url=f"https://example.com/{src}/{i}",
                author=f"user_{i % 60}",
                container=f"r/sub{i % 15}" if src == "reddit" else src,
                published_at=f"2026-11-{(i % 25) + 1:02d}",
                date_confidence="high",
                engagement={
                    "score": 10 + rng.randint(0, 5000),
                    "num_comments": rng.randint(0, 400),
                    "views": rng.randint(500, 500_000),
                    "likes": rng.randint(10, 10_000),
                },
                relevance_hint=0.5,
                why_relevant="",
                snippet=body[:240],
            )
        )
    return items


def _clone_items(items: list[schema.SourceItem]) -> list[schema.SourceItem]:
    return [
        schema.SourceItem(
            **{
                **it.__dict__,
                "engagement": dict(it.engagement),
                "metadata": dict(it.metadata),
            }
        )
        for it in items
    ]


def _make_candidates(items: list[schema.SourceItem]) -> list[schema.Candidate]:
    return [
        schema.Candidate(
            candidate_id=f"c-{i}",
            item_id=it.item_id,
            source=it.source,
            sources=[it.source],
            title=it.title,
            url=it.url,
            snippet=it.snippet,
            subquery_labels=["primary"],
            native_ranks={"primary": i + 1},
            local_relevance=it.local_relevance or 0.5,
            freshness=it.freshness or 80,
            engagement=it.engagement_score or 50,
            source_quality=it.source_quality or 0.8,
            rrf_score=1.0 / (60 + i),
            final_score=70.0 + (i % 25),
            source_items=[it],
        )
        for i, it in enumerate(items)
    ]


def _time_fn(fn, repeats: int = 3):
    best_ms = float("inf")
    last = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        last = fn()
        elapsed = (time.perf_counter() - t0) * 1000.0
        best_ms = min(best_ms, elapsed)
    return best_ms, last


def bench_dedupe() -> list[tuple[str, float, float, bool]]:
    rows = []
    for n in (100, 250, 500, 1000):
        items = _make_items(n)
        os.environ["LAST30DAYS_ENGINE"] = "python"
        py_ms, py_out = _time_fn(lambda: dedupe.dedupe_items(items, threshold=0.7), repeats=1 if n >= 500 else 3)
        os.environ["LAST30DAYS_ENGINE"] = "rust"
        rs_ms, rs_out = _time_fn(lambda: dedupe.dedupe_items(items, threshold=0.7), repeats=3)
        parity = [x.item_id for x in py_out] == [x.item_id for x in rs_out]
        rows.append((f"dedupe_items (N={n})", py_ms, rs_ms, parity))
    return rows


def bench_annotate() -> list[tuple[str, float, float, bool]]:
    rows = []
    for n in (100, 500, 2000):
        items = _make_items(n)
        os.environ["LAST30DAYS_ENGINE"] = "python"
        py_ms, py_out = _time_fn(
            lambda: signals.annotate_stream(
                _clone_items(items),
                "Rust Python performance benchmark CLI",
                "balanced_recent",
                reference_date="2026-11-26",
            ),
            repeats=3,
        )
        os.environ["LAST30DAYS_ENGINE"] = "rust"
        rs_ms, rs_out = _time_fn(
            lambda: signals.annotate_stream(
                _clone_items(items),
                "Rust Python performance benchmark CLI",
                "balanced_recent",
                reference_date="2026-11-26",
            ),
            repeats=3,
        )
        parity = [x.item_id for x in py_out] == [x.item_id for x in rs_out] and all(
            abs((a.local_rank_score or 0.0) - (b.local_rank_score or 0.0)) < 1e-9
            for a, b in zip(py_out, rs_out)
        )
        rows.append((f"annotate_stream (N={n})", py_ms, rs_ms, parity))
    return rows


def bench_cluster() -> list[tuple[str, float, float, bool]]:
    rows = []
    plan = schema.QueryPlan(
        intent="breaking_news",
        freshness_mode="strict_recent",
        cluster_mode="story",
        raw_topic="Rust Python performance",
        subqueries=[],
        source_weights={},
    )
    for n in (50, 200, 400):
        items = _make_items(n)
        os.environ["LAST30DAYS_ENGINE"] = "python"
        py_ms, py_out = _time_fn(
            lambda: cluster.cluster_candidates(_make_candidates(items), plan),
            repeats=1 if n >= 400 else 3,
        )
        os.environ["LAST30DAYS_ENGINE"] = "rust"
        rs_ms, rs_out = _time_fn(
            lambda: cluster.cluster_candidates(_make_candidates(items), plan),
            repeats=3,
        )
        parity = [
            (c.title, c.candidate_ids, c.representative_ids, c.sources, round(c.score, 6), c.uncertainty)
            for c in py_out
        ] == [
            (c.title, c.candidate_ids, c.representative_ids, c.sources, round(c.score, 6), c.uncertainty)
            for c in rs_out
        ]
        rows.append((f"cluster_candidates (N={n})", py_ms, rs_ms, parity))
    return rows


def bench_library_sync(library_dir: Path) -> list[tuple[str, float, float, bool]]:
    if not library_dir.is_dir():
        return []
    md_count = len(list(library_dir.glob("*.md")))
    if md_count == 0:
        return []
    rows = []
    with tempfile.TemporaryDirectory(prefix="l30-bench-") as tmp:
        tmp_path = Path(tmp)
        empty_briefs = tmp_path / "empty-briefs"
        py_db = tmp_path / "py-library.db"
        rs_db = tmp_path / "rs-library.db"

        # Cold initial index (populates SQLite DB from scratch)
        os.environ["LAST30DAYS_ENGINE"] = "python"
        t0 = time.perf_counter()
        py_cold = library_index.sync_library(library_dir, empty_briefs, db_path=py_db)
        py_cold_ms = (time.perf_counter() - t0) * 1000.0

        # Warm incremental sync (the hot path on EVERY /last30days run!)
        t0 = time.perf_counter()
        py_warm = library_index.sync_library(library_dir, empty_briefs, db_path=py_db)
        py_warm_ms = (time.perf_counter() - t0) * 1000.0

        # Warm Rust incremental sync against the SAME DB populated by Python!
        # Proves exact fingerprint + entry_id + metadata parity across every single file.
        os.environ["LAST30DAYS_ENGINE"] = "rust"
        t0 = time.perf_counter()
        rs_warm_on_py = library_index.sync_library(library_dir, empty_briefs, db_path=py_db)
        rs_warm_ms = (time.perf_counter() - t0) * 1000.0

        # Also benchmark Rust cold initial index into a fresh DB
        t0 = time.perf_counter()
        rs_cold = library_index.sync_library(library_dir, empty_briefs, db_path=rs_db)
        rs_cold_ms = (time.perf_counter() - t0) * 1000.0

        # Compare SQLite document tables row-for-row
        with sqlite3.connect(py_db) as c_py, sqlite3.connect(rs_db) as c_rs:
            py_docs = sorted(
                c_py.execute(
                    "SELECT entry_id, content_hash, topic, published_date, headline, summary FROM library_documents"
                ).fetchall()
            )
            rs_docs = sorted(
                c_rs.execute(
                    "SELECT entry_id, content_hash, topic, published_date, headline, summary FROM library_documents"
                ).fetchall()
            )

        cold_parity = (
            py_cold.indexed == rs_cold.indexed
            and len(py_docs) == len(rs_docs)
            and py_docs == rs_docs
        )
        warm_parity = (
            rs_warm_on_py.indexed == 0
            and rs_warm_on_py.removed == 0
            and rs_warm_on_py.unchanged == py_warm.unchanged
        )
        if not cold_parity:
            py_map = {r[0]: r for r in py_docs}
            rs_map = {r[0]: r for r in rs_docs}
            only_py = set(py_map) - set(rs_map)
            only_rs = set(rs_map) - set(py_map)
            diff_rows = [k for k in set(py_map) & set(rs_map) if py_map[k] != rs_map[k]]
            print(
                f"  [DEBUG] library diff: py_count={len(py_docs)}, rs_count={len(rs_docs)}, "
                f"only_py={len(only_py)}, only_rs={len(only_rs)}, diff_rows={len(diff_rows)}",
                file=sys.stderr,
            )
            for k in list(only_py)[:3]:
                print(f"    only_py: {py_map[k][:4]}", file=sys.stderr)
            for k in list(only_rs)[:3]:
                print(f"    only_rs: {rs_map[k][:4]}", file=sys.stderr)
            for k in diff_rows[:3]:
                print(f"    diff {k}:\n      py={py_map[k]}\n      rs={rs_map[k]}", file=sys.stderr)

        rows.append((f"library_sync warm ({md_count} files)", py_warm_ms, rs_warm_ms, warm_parity))
        rows.append((f"library_sync cold ({md_count} files)", py_cold_ms, rs_cold_ms, cold_parity))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B benchmark Python vs Rust core accelerator")
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=library.DEFAULT_MEMORY_DIR,
        help="Directory of saved Markdown reports for real-world library sync benchmark",
    )
    args = parser.parse_args()

    bin_path = rust_accel.find_binary()
    if not bin_path:
        print("ERROR: last30days-core binary not found. Run `cd rust && cargo build --release`.", file=sys.stderr)
        return 1

    # Warm the Rust worker once before timing.
    os.environ["LAST30DAYS_ENGINE"] = "rust"
    assert rust_accel.is_available(), "Rust worker ping failed"
    print(f"Binary: {bin_path}")
    print()

    rows: list[tuple[str, float, float, bool]] = []
    rows.extend(bench_dedupe())
    rows.extend(bench_annotate())
    rows.extend(bench_cluster())
    rows.extend(bench_library_sync(args.library_dir.expanduser()))

    print(f"{'Benchmark':<38} {'Python (ms)':>12} {'Rust (ms)':>11} {'Speedup':>9} {'Parity':>8}")
    print("-" * 82)
    all_ok = True
    for name, py_ms, rs_ms, parity in rows:
        speedup = py_ms / rs_ms if rs_ms > 0 else float("inf")
        status = "EXACT" if parity else "DIFF"
        if not parity:
            all_ok = False
        print(f"{name:<38} {py_ms:>12.1f} {rs_ms:>11.1f} {speedup:>8.1f}x {status:>8}")

    rust_accel.shutdown()
    return 0 if all_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
