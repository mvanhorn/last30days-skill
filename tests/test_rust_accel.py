"""Differential parity and fallback tests for the Rust core accelerator."""

from __future__ import annotations

import hashlib
import http.client
import os
import random
import stat
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from lib import cluster, dedupe, doctor, library, library_index, rust_accel, schema, setup_wizard, signals


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


@requires_rust
def test_library_sync_nested_emphasis_titles_match_and_persist(tmp_path: Path, monkeypatch):
    memory = tmp_path / "memory"
    memory.mkdir()
    briefs = tmp_path / "briefs"
    (memory / "nested-a-raw.md").write_text(
        "# **__Rust__**\n\n- Date range: 2026-10-27 to 2026-11-26\n\nNested bold underscore body.\n",
        encoding="utf-8",
    )
    (memory / "nested-b-raw.md").write_text(
        "# __**Rust**__\n\n- Date range: 2026-10-27 to 2026-11-26\n\nNested underscore bold body.\n",
        encoding="utf-8",
    )
    (memory / "mixed-bold-raw.md").write_text(
        "# **bold** and __bold2__\n\n- Date range: 2026-10-27 to 2026-11-26\n\nSeparate bold tokens.\n",
        encoding="utf-8",
    )

    py_db = tmp_path / "py.db"
    rs_db = tmp_path / "rs.db"

    monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
    py_sync = library_index.sync_library(memory, briefs, db_path=py_db)
    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    rs_sync = library_index.sync_library(memory, briefs, db_path=rs_db)

    assert (rs_sync.indexed, rs_sync.removed, rs_sync.unchanged) == (3, 0, 0)
    assert (py_sync.indexed, py_sync.removed, py_sync.unchanged) == (3, 0, 0)

    # Repeated warm sync must retain all rows with zero churn.
    rs_sync_2 = library_index.sync_library(memory, briefs, db_path=rs_db)
    assert (rs_sync_2.indexed, rs_sync_2.removed, rs_sync_2.unchanged) == (0, 0, 3)

    # Cross-sync against Python's DB must also agree on every entry_id and fingerprint.
    rs_on_py_db = library_index.sync_library(memory, briefs, db_path=py_db)
    assert (rs_on_py_db.indexed, rs_on_py_db.removed, rs_on_py_db.unchanged) == (0, 0, 3)

    matches = library_index.search("Nested", db_path=rs_db, store_db_path=tmp_path / "none.db")
    assert len(matches) == 2


@requires_rust
@pytest.mark.skipif(os.name != "posix", reason="POSIX directory symlink swap regression")
def test_library_sync_rust_rejects_parent_symlink_swap_before_upsert(tmp_path: Path, monkeypatch):
    memory = tmp_path / "memory"
    finished = memory / "finished"
    finished.mkdir(parents=True)
    briefs = tmp_path / "briefs"
    outside = tmp_path / "outside"
    outside.mkdir()

    (finished / "target.md").write_text(
        "# Original Safe Brief\n\n- Date range: 2026-10-27 to 2026-11-26\n\nSafe content.\n",
        encoding="utf-8",
    )
    (outside / "target.md").write_text(
        "# Outside Secret Brief\n\n- Date range: 2026-10-27 to 2026-11-26\n\nLeaked outside secret payload.\n",
        encoding="utf-8",
    )

    real_scan = rust_accel.scan_markdown_files
    swapped = False

    def _swap_after_scan(requests):
        nonlocal swapped
        result = real_scan(requests)
        if not swapped:
            swapped = True
            moved = tmp_path / "finished_backup"
            finished.rename(moved)
            os.symlink(outside, finished)
        return result

    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    monkeypatch.setattr(rust_accel, "scan_markdown_files", _swap_after_scan)

    db_path = tmp_path / "lib.db"
    synced = library_index.sync_library(memory, briefs, db_path=db_path)

    assert swapped is True
    assert synced.indexed == 0
    assert any("Skipped" in note for note in synced.notes)
    assert library_index.search("Leaked", db_path=db_path, store_db_path=tmp_path / "none.db") == []
    assert library_index.search("Outside", db_path=db_path, store_db_path=tmp_path / "none.db") == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable script helper")
def test_worker_timeout_falls_back_in_auto_and_raises_in_rust(tmp_path: Path, monkeypatch):
    stall_script = tmp_path / "stall-worker"
    stall_script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('op') == 'ping':\n"
        "        sys.stdout.write(json.dumps({'status': 'ok', 'protocol': 1}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "    else:\n"
        "        time.sleep(60)\n",
        encoding="utf-8",
    )
    stall_script.chmod(stall_script.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setenv("LAST30DAYS_RUST_BIN", str(stall_script))
    monkeypatch.setenv("LAST30DAYS_RUST_TIMEOUT", "0.25")
    rust_accel.shutdown()
    rust_accel._POOL._disabled_for_bin = None
    try:
        # Auto mode: kills stalled worker, returns None, and falls back to pure Python.
        monkeypatch.setenv("LAST30DAYS_ENGINE", "auto")
        t0 = time.monotonic()
        res = rust_accel.dedupe_kept_indices([{"mode": "fuzzy", "text": "hello"}], 0.7)
        elapsed = time.monotonic() - t0
        assert res is None
        assert elapsed < 3.0
        assert rust_accel._POOL._proc is None
        assert rust_accel._POOL._disabled_for_bin == str(stall_script)

        # Forced rust mode: raises RustAccelError within the deadline.
        rust_accel.shutdown()
        rust_accel._POOL._disabled_for_bin = None
        monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
        t1 = time.monotonic()
        with pytest.raises(rust_accel.RustAccelError, match="timed out"):
            rust_accel.dedupe_kept_indices([{"mode": "fuzzy", "text": "hello"}], 0.7)
        assert time.monotonic() - t1 < 3.0
        assert rust_accel._POOL._proc is None
    finally:
        rust_accel.shutdown()
        rust_accel._POOL._disabled_for_bin = None


@requires_rust
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO required")
def test_rust_scan_rejects_fifo_without_blocking(tmp_path: Path, monkeypatch):
    fifo_path = tmp_path / "named-pipe.md"
    os.mkfifo(fifo_path)

    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    monkeypatch.setenv("LAST30DAYS_RUST_TIMEOUT", "2.0")
    t0 = time.monotonic()
    outcomes = rust_accel.scan_markdown_files(
        [
            {
                "path": str(fifo_path),
                "stem": "named-pipe",
                "source_format": "markdown",
                "expected_dev_ino": None,
                "fallback_date": "2026-11-26",
            }
        ]
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5
    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "skipped"


def test_platform_asset_name_and_checksums_and_url_validation():
    assert rust_accel.platform_asset_name("darwin", "arm64") == "last30days-core-darwin-arm64"
    assert rust_accel.platform_asset_name("darwin", "x86_64") == "last30days-core-darwin-amd64"
    assert rust_accel.platform_asset_name("linux", "x86_64") == "last30days-core-linux-amd64"
    assert rust_accel.platform_asset_name("linux", "aarch64") == "last30days-core-linux-arm64"
    assert rust_accel.platform_asset_name("win32", "AMD64") == "last30days-core-windows-amd64.exe"
    assert rust_accel.platform_asset_name("freebsd", "x86_64") is None

    valid_hex = "a" * 64
    parsed = rust_accel.parse_checksums(
        f"# comment\n"
        f"{valid_hex}  last30days-core-darwin-arm64\n"
        f"{valid_hex} *last30days-core-windows-amd64.exe\n"
        f"short  ignored-bin\n"
        f"{valid_hex}  ../escape-bin\n"
    )
    assert parsed == {
        "last30days-core-darwin-arm64": valid_hex,
        "last30days-core-windows-amd64.exe": valid_hex,
    }

    assert rust_accel._validate_download_url(
        "https://github.com/mvanhorn/last30days-skill/releases/download/v4.7.0/last30days-core-darwin-arm64"
    ).startswith("https://")
    for bad_url in (
        "http://github.com/mvanhorn/last30days-skill/releases/download/v4.7.0/bin",
        "https://evil.example.com/last30days-core-darwin-arm64",
        "https://user:pass@github.com/mvanhorn/last30days-skill/releases/download/v4.7.0/bin",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError, match="disallowed download URL"):
            rust_accel._validate_download_url(bad_url)


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable stub installer test")
def test_install_release_binary_verifies_sha256_installs_upgrades_and_rejects_bad_digest(
    tmp_path: Path, monkeypatch
):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LAST30DAYS_ENGINE", "auto")
    monkeypatch.delenv("LAST30DAYS_RUST_BIN", raising=False)

    # Hide dev checkout binary so discovery uses only managed ~/.local/share/last30days/bin/
    managed = rust_accel.install_bin_path()
    monkeypatch.setattr(rust_accel, "_candidate_binary_paths", lambda: [managed])
    monkeypatch.setattr(rust_accel, "platform_asset_name", lambda *_a, **_k: "last30days-core-test-bin")

    good_script = (
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    if json.loads(line).get('op') == 'ping':\n"
        "        sys.stdout.write(json.dumps({'status': 'ok', 'version': '0.1.0', 'protocol': 1}) + '\\n')\n"
        "        sys.stdout.flush()\n"
    ).encode("utf-8")
    good_sha = hashlib.sha256(good_script).hexdigest()
    checksums_text = f"{good_sha}  last30days-core-test-bin\n".encode("utf-8")

    fetch_calls: list[str] = []

    def _fake_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        fetch_calls.append(url)
        if url.endswith(rust_accel.CHECKSUMS_ASSET):
            return checksums_text
        if url.endswith("last30days-core-test-bin"):
            return good_script
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(rust_accel, "_https_fetch_bytes", _fake_fetch)

    try:
        # 1. Fresh install succeeds with SHA-256 + ping verification.
        res1 = rust_accel.install_release_binary(version="4.7.0")
        assert res1["installed"] is True
        assert res1["action"] == "installed"
        assert res1["version"] == "4.7.0"
        assert managed.is_file()
        assert len(fetch_calls) == 2

        # 2. Same version is idempotent with zero downloads.
        res2 = rust_accel.install_release_binary(version="4.7.0")
        assert res2["installed"] is True
        assert res2["action"] == "already_installed"
        assert len(fetch_calls) == 2

        # 3. Version bump triggers verified upgrade.
        res3 = rust_accel.install_release_binary(version="4.8.0")
        assert res3["installed"] is True
        assert res3["action"] == "upgraded"
        assert res3["version"] == "4.8.0"
        assert len(fetch_calls) == 4

        # 4. Doctor status reports active rust core without network calls.
        st = rust_accel.status()
        assert st["active"] is True
        assert st["installed_version"] == "4.8.0"
        assert st["fallback"] is None

        # 5. Tampered binary fails SHA-256 check and keeps existing binary intact.
        def _tampered_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
            if url.endswith(rust_accel.CHECKSUMS_ASSET):
                return checksums_text
            return b"corrupted payload"

        monkeypatch.setattr(rust_accel, "_https_fetch_bytes", _tampered_fetch)
        res4 = rust_accel.install_release_binary(version="4.9.0")
        assert res4["action"] == "install_failed"
        assert "SHA-256 mismatch" in res4["detail"]
        assert res4["installed"] is True
        assert managed.read_bytes() == good_script
    finally:
        rust_accel.shutdown()


def test_ensure_installed_for_run_records_cooldown_on_failure(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LAST30DAYS_ENGINE", "auto")
    managed = rust_accel.install_bin_path()
    monkeypatch.setattr(rust_accel, "_candidate_binary_paths", lambda: [managed])
    monkeypatch.setattr(rust_accel, "platform_asset_name", lambda *_a, **_k: "last30days-core-test-bin")
    monkeypatch.setattr(rust_accel, "expected_skill_version", lambda: "4.7.0")

    attempts = 0

    def _failing_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        nonlocal attempts
        attempts += 1
        raise OSError("network unreachable")

    monkeypatch.setattr(rust_accel, "_https_fetch_bytes", _failing_fetch)

    first = rust_accel.ensure_installed_for_run()
    assert first is not None and first["action"] == "install_failed"
    assert attempts == 1

    # Second call within cooldown must skip network entirely.
    second = rust_accel.ensure_installed_for_run()
    assert second is None
    assert attempts == 1


def test_install_release_binary_catches_incomplete_read(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("LAST30DAYS_ENGINE", "auto")
    managed = rust_accel.install_bin_path()
    monkeypatch.setattr(rust_accel, "_candidate_binary_paths", lambda: [managed])
    monkeypatch.setattr(rust_accel, "platform_asset_name", lambda *_a, **_k: "last30days-core-test-bin")
    monkeypatch.setattr(rust_accel, "expected_skill_version", lambda: "4.7.0")

    def _truncated_fetch(url: str, *, max_bytes: int, timeout: float = 30.0) -> bytes:
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(rust_accel, "_https_fetch_bytes", _truncated_fetch)

    res = rust_accel.ensure_installed_for_run()
    assert res is not None
    assert res["installed"] is False
    assert res["action"] == "install_failed"
    assert not managed.exists()


def test_expected_skill_version_reads_mcp_sentinel_without_skill_md(tmp_path: Path):
    bundle_root = tmp_path / "last30days-pp-mcp" / "v4.7.0"
    fake_mod = bundle_root / "lib" / "rust_accel.py"
    fake_mod.parent.mkdir(parents=True)
    fake_mod.write_text("", encoding="utf-8")
    (bundle_root / ".version").write_text("v4.7.0\n", encoding="utf-8")

    assert rust_accel.expected_skill_version(fake_mod) == "4.7.0"

    (bundle_root / ".version").write_text("dev\n", encoding="utf-8")
    assert rust_accel.expected_skill_version(fake_mod) is None


@requires_rust
def test_library_sync_rust_preserves_distinct_topics_sharing_slug(tmp_path: Path, monkeypatch):
    memory = tmp_path / "memory"
    finished = memory / "finished"
    finished.mkdir(parents=True)
    briefs = tmp_path / "briefs"
    (finished / "c-2026-07-10.md").write_text(
        "🌐 last30days · C# · Jun 10 to Jul 10, 2026 · v4.6.0\n\nC# finished brief.\n",
        encoding="utf-8",
    )
    (memory / "c-raw.md").write_text(
        "# last30days v4.6.0: C++\n\n- Date range: 2026-06-10 to 2026-07-10\n\nC++ raw report.\n",
        encoding="utf-8",
    )
    (memory / "c-raw-2026-07-10.md").write_text(
        "# last30days v4.6.0: c#\n\n- Date range: 2026-06-10 to 2026-07-10\n\nSuperseded C# raw.\n",
        encoding="utf-8",
    )

    py_db = tmp_path / "py.db"
    rs_db = tmp_path / "rs.db"
    monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
    py_sync = library_index.sync_library(memory, briefs, db_path=py_db)
    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    rs_sync = library_index.sync_library(memory, briefs, db_path=rs_db)

    assert (rs_sync.indexed, rs_sync.removed, rs_sync.unchanged) == (2, 0, 0)
    assert (py_sync.indexed, py_sync.removed, py_sync.unchanged) == (2, 0, 0)
    found_topics = {
        m.topic
        for m in library_index.search("C", db_path=rs_db, store_db_path=tmp_path / "none.db")
    }
    assert found_topics == {"C#", "C++"}
    assert len(library_index.search("Superseded", db_path=rs_db, store_db_path=tmp_path / "none.db")) == 0


@requires_rust
def test_v2ex_signal_parity(monkeypatch):
    items_py = [
        schema.SourceItem(
            item_id="v2ex-1",
            source="v2ex",
            title="Rust async runtime discussion",
            body="Comparing Tokio and smol in production.",
            url="https://www.v2ex.com/t/12345",
            published_at="2026-11-25",
            engagement={"replies": 85},
        )
    ]
    items_rs = [
        schema.SourceItem(**{**items_py[0].__dict__, "engagement": dict(items_py[0].engagement)})
    ]
    monkeypatch.setenv("LAST30DAYS_ENGINE", "python")
    py_res = signals.annotate_stream(items_py, "Rust async runtime", "balanced_recent", reference_date="2026-11-26")
    monkeypatch.setenv("LAST30DAYS_ENGINE", "rust")
    rs_res = signals.annotate_stream(items_rs, "Rust async runtime", "balanced_recent", reference_date="2026-11-26")
    assert rs_res[0].source_quality == pytest.approx(py_res[0].source_quality, abs=1e-9)
    assert rs_res[0].engagement_score == py_res[0].engagement_score
    assert rs_res[0].local_rank_score == pytest.approx(py_res[0].local_rank_score, abs=1e-9)


def test_setup_status_and_doctor_render_rust_core(monkeypatch):
    text = setup_wizard.get_setup_status_text(
        {
            "cookies_found": {},
            "ytdlp_installed": True,
            "ytdlp_action": "already_installed",
            "rust_core": {"installed": True, "action": "installed", "version": "4.7.0"},
        }
    )
    assert "Installed native Rust core (v4.7.0)" in text

    monkeypatch.setattr(
        rust_accel,
        "status",
        lambda: {
            "mode": "auto",
            "active": True,
            "binary_path": "/tmp/last30days-core",
            "installed_version": "4.7.0",
            "expected_version": "4.7.0",
            "platform_asset": "last30days-core-darwin-arm64",
            "fallback": None,
        },
    )
    report = doctor.build_report({})
    assert report["rust_core"]["active"] is True
    rendered = doctor.render_text(report)
    assert "compute core: rust (last30days-core v4.7.0, /tmp/last30days-core)" in rendered
