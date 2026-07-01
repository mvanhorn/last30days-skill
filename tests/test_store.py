"""Tests for store.py - SQLite research accumulator and watchlist storage."""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Import the module under test

import store
from lib import schema

@pytest.fixture


def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    
    # Override the database path
    original_override = store._db_override
    store._db_override = db_path
    
    # Initialize fresh database
    store.init_db()
    
    yield db_path
    
    # Cleanup
    store._db_override = original_override
    if db_path.exists():
        db_path.unlink()

@pytest.fixture


def sample_report():
    """Create a sample Report with multiple sources including HN and Polymarket."""
    return schema.report_from_dict({
        "topic": "Test Topic",
        "range_from": "2026-01-01",
        "range_to": "2026-04-03",
        "generated_at": "2026-04-03T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "gemini",
            "planner_model": "gemini-2.0-flash-exp",
            "rerank_model": "gemini-2.0-flash-exp",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": "test",
            "subqueries": [],
            "source_weights": {},
        },
        "clusters": [],
        "ranked_candidates": [
            {
                "candidate_id": "c-r1",
                "item_id": "R1",
                "source": "reddit",
                "title": "Test Reddit Post",
                "url": "https://reddit.com/r/test/1",
                "snippet": "Reddit snippet",
                "subquery_labels": ["primary"],
                "native_ranks": {"reddit": 1},
                "local_relevance": 0.8,
                "freshness": 100,
                "engagement": 50.0,
                "source_quality": 0.8,
                "rrf_score": 1.0,
                "final_score": 0.8,
                "explanation": "Reddit snippet",
                "source_items": [
                    {
                        "item_id": "R1",
                        "source": "reddit",
                        "title": "Test Reddit Post",
                        "body": "Reddit discussion content",
                        "url": "https://reddit.com/r/test/1",
                        "author": "testuser",
                        "engagement_score": 50.0,
                        "local_relevance": 0.8,
                        "snippet": "Reddit snippet",
                    }
                ],
            },
            {
                "candidate_id": "c-x1",
                "item_id": "X1",
                "source": "x",
                "title": "Test X Post",
                "url": "https://x.com/test/status/1",
                "snippet": "X snippet",
                "subquery_labels": ["primary"],
                "native_ranks": {"x": 1},
                "local_relevance": 0.85,
                "freshness": 100,
                "engagement": 75.0,
                "source_quality": 0.8,
                "rrf_score": 1.0,
                "final_score": 0.85,
                "explanation": "X snippet",
                "source_items": [
                    {
                        "item_id": "X1",
                        "source": "x",
                        "title": "Test X Post",
                        "body": "X post content",
                        "url": "https://x.com/test/status/1",
                        "author": "xuser",
                        "engagement_score": 75.0,
                        "local_relevance": 0.85,
                        "snippet": "X snippet",
                    }
                ],
            },
        ],
        "items_by_source": {
            "reddit": [
                {
                    "item_id": "R1",
                    "source": "reddit",
                    "title": "Test Reddit Post",
                    "body": "Reddit discussion content",
                    "url": "https://reddit.com/r/test/1",
                    "author": "testuser",
                    "engagement_score": 50.0,
                    "local_relevance": 0.8,
                    "snippet": "Reddit snippet",
                }
            ],
            "x": [
                {
                    "item_id": "X1",
                    "source": "x",
                    "title": "Test X Post",
                    "body": "X post content",
                    "url": "https://x.com/test/status/1",
                    "author": "xuser",
                    "engagement_score": 75.0,
                    "local_relevance": 0.85,
                    "snippet": "X snippet",
                }
            ],
            "hackernews": [
                {
                    "item_id": "HN1",
                    "source": "hackernews",
                    "title": "Test HN Story",
                    "body": "HN story content with comments",
                    "url": "https://news.ycombinator.com/item?id=12345",
                    "author": "hnuser",
                    "engagement_score": 120.0,
                    "local_relevance": 0.9,
                    "snippet": "HN snippet",
                }
            ],
            "polymarket": [
                {
                    "item_id": "PM1",
                    "source": "polymarket",
                    "title": "Will event happen?",
                    "body": "Yes: 64% / No: 36%",
                    "url": "https://polymarket.com/event/test-event",
                    "author": None,
                    "engagement_score": 342000.0,
                    "local_relevance": 0.7,
                    "snippet": "Prediction market",
                }
            ],
        },
        "errors_by_source": {},
        "warnings": [],
    })

# === Tests for findings_from_report() ===


def test_findings_from_report_processes_all_sources(sample_report):
    """Test that findings_from_report extracts items from all sources in items_by_source."""
    findings = store.findings_from_report(sample_report)
    
    # Should have 4 findings (reddit + x + hackernews + polymarket)
    assert len(findings) == 4
    
    # Check all sources are present
    sources = {f["source"] for f in findings}
    assert sources == {"reddit", "x", "hackernews", "polymarket"}


def test_findings_from_report_includes_hackernews(sample_report):
    """Test that HN items are extracted correctly (PR #85 feature)."""
    findings = store.findings_from_report(sample_report)
    
    hn_findings = [f for f in findings if f["source"] == "hackernews"]
    assert len(hn_findings) == 1
    
    hn = hn_findings[0]
    assert hn["source_url"] == "https://news.ycombinator.com/item?id=12345"
    assert hn["source_title"] == "Test HN Story"
    assert hn["engagement_score"] == 120.0
    assert hn["relevance_score"] == 0.9
    assert "HN story content" in hn["content"]


def test_findings_from_report_includes_polymarket(sample_report):
    """Test that Polymarket items are extracted correctly (PR #85 feature)."""
    findings = store.findings_from_report(sample_report)
    
    pm_findings = [f for f in findings if f["source"] == "polymarket"]
    assert len(pm_findings) == 1
    
    pm = pm_findings[0]
    assert pm["source_url"] == "https://polymarket.com/event/test-event"
    assert pm["source_title"] == "Will event happen?"
    assert pm["engagement_score"] == 342000.0
    assert pm["relevance_score"] == 0.7
    assert "Yes: 64%" in pm["content"]


def test_findings_from_report_respects_limit(sample_report):
    """Test that limit parameter works correctly."""
    findings = store.findings_from_report(sample_report, limit=2)
    
    # Should have at most 2 items per source
    source_counts = {}
    for f in findings:
        source = f["source"]
        source_counts[source] = source_counts.get(source, 0) + 1
    
    for count in source_counts.values():
        assert count <= 2


def test_findings_from_report_handles_empty_sources():
    """Test that empty sources in items_by_source don't cause issues."""
    report = schema.report_from_dict({
        "topic": "Test",
        "range_from": "2026-01-01",
        "range_to": "2026-04-03",
        "generated_at": "2026-04-03T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "gemini",
            "planner_model": "gemini-2.0-flash-exp",
            "rerank_model": "gemini-2.0-flash-exp",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": "test",
            "subqueries": [],
            "source_weights": {},
        },
        "clusters": [],
        "ranked_candidates": [],
        "items_by_source": {
            "reddit": [],
            "x": [],
            "hackernews": [],
            "polymarket": [],
        },
        "errors_by_source": {},
        "warnings": [],
    })
    
    findings = store.findings_from_report(report)
    assert len(findings) == 0


def test_findings_from_report_handles_missing_fields():
    """Test that missing optional fields (author, snippet) are handled gracefully."""
    report = schema.report_from_dict({
        "topic": "Test",
        "range_from": "2026-01-01",
        "range_to": "2026-04-03",
        "generated_at": "2026-04-03T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "gemini",
            "planner_model": "gemini-2.0-flash-exp",
            "rerank_model": "gemini-2.0-flash-exp",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": "test",
            "subqueries": [],
            "source_weights": {},
        },
        "clusters": [],
        "ranked_candidates": [],
        "items_by_source": {
            "hackernews": [
                {
                    "item_id": "R1",
                    "source": "hackernews",
                    "title": "Test",
                    "body": "Content",
                    "url": "https://news.ycombinator.com/item?id=1",
                    "author": None,  # Missing author
                    "engagement_score": None,  # Missing engagement
                    "local_relevance": None,  # Missing relevance
                    "snippet": None,  # Missing snippet
                }
            ],
        },
        "errors_by_source": {},
        "warnings": [],
    })
    
    findings = store.findings_from_report(report)
    assert len(findings) == 1
    
    f = findings[0]
    assert f["author"] == ""
    assert f["engagement_score"] == 0.0
    assert f["relevance_score"] == 0.5
    assert f["summary"] == "Content"  # Falls back to body

# === Tests for store_findings() ===


def test_store_findings_basic(temp_db, sample_report):
    """Test basic storage of findings."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    
    findings = store.findings_from_report(sample_report)
    counts = store.store_findings(run_id, topic["id"], findings)
    
    assert counts["new"] == 4
    assert counts["updated"] == 0
    
    # Verify in database
    conn = sqlite3.connect(str(temp_db))
    total = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    assert total == 4
    conn.close()


def test_store_findings_deduplicates_by_url(temp_db, sample_report):
    """Test that duplicate URLs are detected and updated, not duplicated."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    
    findings = store.findings_from_report(sample_report)
    
    # Store once
    counts1 = store.store_findings(run_id, topic["id"], findings)
    assert counts1["new"] == 4
    assert counts1["updated"] == 0
    
    # Store again (same URLs)
    counts2 = store.store_findings(run_id, topic["id"], findings)
    assert counts2["new"] == 0
    assert counts2["updated"] == 4
    
    # Verify total count didn't double
    conn = sqlite3.connect(str(temp_db))
    total = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    assert total == 4
    conn.close()


def test_store_findings_updates_engagement_on_resighting(temp_db, sample_report):
    """Test that re-sighting a finding updates engagement score if higher."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    
    findings = store.findings_from_report(sample_report)
    
    # Store with initial engagement
    store.store_findings(run_id, topic["id"], findings)
    
    # Modify engagement score for HN finding
    hn_finding = next(f for f in findings if f["source"] == "hackernews")
    hn_finding["engagement_score"] = 200.0  # Higher than original 120.0
    
    # Store again
    store.store_findings(run_id, topic["id"], [hn_finding])
    
    # Verify engagement was updated
    conn = sqlite3.connect(str(temp_db))
    score = conn.execute(
        "SELECT engagement_score FROM findings WHERE source='hackernews'"
    ).fetchone()[0]
    assert score == 200.0
    conn.close()


def test_store_findings_increments_sighting_count(temp_db, sample_report):
    """Test that re-sighting increments sighting_count."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    
    findings = store.findings_from_report(sample_report)
    
    # Store once
    store.store_findings(run_id, topic["id"], findings)
    
    # Store again
    store.store_findings(run_id, topic["id"], findings)
    
    # Verify sighting_count
    conn = sqlite3.connect(str(temp_db))
    counts = conn.execute(
        "SELECT sighting_count FROM findings"
    ).fetchall()
    
    for (count,) in counts:
        assert count == 2  # Seen twice
    conn.close()


def test_store_findings_upserts_on_concurrent_duplicate_url(temp_db, monkeypatch):
    """A stale dedup read (a concurrent run that inserted the same URL between
    our SELECT and INSERT) must upsert, not raise IntegrityError and lose the
    whole batch. Regression for the SELECT-then-INSERT race in store_findings."""
    topic = store.add_topic("Test Topic")
    finding = {
        "source": "reddit",
        "source_url": "https://reddit.com/race",
        "source_title": "Race",
        "engagement_score": 5.0,
    }

    # First run inserts the URL.
    run1 = store.record_run(topic["id"], source_mode="v3")
    store.store_findings(run1, topic["id"], [finding])

    # Force the dedup lookup to miss the now-existing URL, so store_findings
    # takes the INSERT path for a row that is already present — exactly what a
    # racing writer's stale read produces.
    real_connect = store._connect
    dedup_prefix = (
        "SELECT id, source_url, engagement_score FROM findings WHERE source_url IN"
    )

    class StaleReadConn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            if sql.strip().startswith(dedup_prefix):
                return self._conn.execute(
                    "SELECT id, source_url, engagement_score FROM findings WHERE 0"
                )
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(
        store, "_connect", lambda *a, **k: StaleReadConn(real_connect(*a, **k))
    )

    run2 = store.record_run(topic["id"], source_mode="v3")
    # Without ON CONFLICT this raises sqlite3.IntegrityError on the UNIQUE
    # source_url and rolls back the batch.
    counts = store.store_findings(run2, topic["id"], [{**finding, "engagement_score": 9.0}])

    # The conflict-resolved row is an update, not a new finding. The counters
    # must reflect that, not inflate findings_new.
    assert counts == {"new": 0, "updated": 1}

    conn = sqlite3.connect(str(temp_db))
    rows = conn.execute(
        "SELECT engagement_score, sighting_count FROM findings WHERE source_url = ?",
        ("https://reddit.com/race",),
    ).fetchall()
    run_counts = conn.execute(
        "SELECT findings_new, findings_updated FROM research_runs WHERE id = ?",
        (run2,),
    ).fetchone()
    conn.close()

    assert len(rows) == 1  # not duplicated, not crashed
    assert rows[0][0] == 9.0  # engagement upgraded via max()
    assert rows[0][1] == 2  # sighting_count bumped by the conflict update
    assert run_counts == (0, 1)  # research_runs counters not inflated


def test_store_findings_skips_items_without_url(temp_db):
    """Test that findings without URLs are skipped."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    
    findings = [
        {
            "source": "reddit",
            "source_url": None,  # Missing URL
            "source_title": "Test",
            "content": "Content",
        },
        {
            "source": "reddit",
            "source_url": "https://reddit.com/1",  # Has URL
            "source_title": "Test 2",
            "content": "Content 2",
        },
    ]
    
    counts = store.store_findings(run_id, topic["id"], findings)
    
    # Only the one with URL should be stored
    assert counts["new"] == 1


def test_init_db_creates_finding_sightings_table(temp_db):
    """Test that the per-run sightings ledger is available on fresh databases."""
    conn = sqlite3.connect(str(temp_db))
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='finding_sightings'"
    ).fetchone()
    columns = {
        row[1]: row[3]
        for row in conn.execute("PRAGMA table_info(finding_sightings)").fetchall()
    }
    conn.close()

    assert table is not None
    assert columns["finding_id"] == 1


def test_store_findings_records_sightings_for_new_findings(temp_db):
    """Test that each stored finding is linked to the run that observed it."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    findings = [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/1",
            "source_title": "Reddit 1",
            "content": "Content 1",
            "engagement_score": 10.0,
            "relevance_score": 0.7,
        },
        {
            "source": "x",
            "source_url": "https://x.com/a/status/1",
            "source_title": "X 1",
            "content": "Content 2",
            "engagement_score": 20.0,
            "relevance_score": 0.8,
        },
    ]

    store.store_findings(run_id, topic["id"], findings)

    sightings = store.get_sightings_for_run(topic["id"], run_id)
    assert [s["source_url"] for s in sightings] == [
        "https://reddit.com/1",
        "https://x.com/a/status/1",
    ]
    assert {s["source"] for s in sightings} == {"reddit", "x"}


def test_store_findings_records_sightings_for_resighted_findings(temp_db):
    """Test that a re-seen finding is recorded for each run that observes it."""
    topic = store.add_topic("Test Topic")
    first_run_id = store.record_run(topic["id"], source_mode="v3")
    second_run_id = store.record_run(topic["id"], source_mode="v3")
    finding = {
        "source": "reddit",
        "source_url": "https://reddit.com/1",
        "source_title": "Reddit 1",
        "content": "Content",
        "engagement_score": 10.0,
        "relevance_score": 0.7,
    }

    store.store_findings(first_run_id, topic["id"], [finding])
    store.store_findings(second_run_id, topic["id"], [{**finding, "engagement_score": 15.0}])

    first_sightings = store.get_sightings_for_run(topic["id"], first_run_id)
    second_sightings = store.get_sightings_for_run(topic["id"], second_run_id)

    assert len(first_sightings) == 1
    assert len(second_sightings) == 1
    assert first_sightings[0]["source_url"] == second_sightings[0]["source_url"]
    assert second_sightings[0]["engagement_score"] == 15.0


def test_store_findings_sightings_are_idempotent_per_run(temp_db):
    """Test that storing the same finding twice for one run does not duplicate sightings."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    finding = {
        "source": "reddit",
        "source_url": "https://reddit.com/1",
        "source_title": "Reddit 1",
        "content": "Content",
        "engagement_score": 10.0,
        "relevance_score": 0.7,
    }

    store.store_findings(run_id, topic["id"], [finding])
    store.store_findings(run_id, topic["id"], [finding])

    sightings = store.get_sightings_for_run(topic["id"], run_id)
    assert len(sightings) == 1


def test_store_findings_updates_existing_sighting_for_same_run(temp_db):
    """Test that retrying a run refreshes its sighting snapshot instead of freezing it."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    finding = {
        "source": "reddit",
        "source_url": "https://reddit.com/1",
        "source_title": "Reddit 1",
        "content": "Content",
        "engagement_score": 10.0,
        "relevance_score": 0.7,
    }

    store.store_findings(run_id, topic["id"], [finding])
    store.store_findings(
        run_id,
        topic["id"],
        [{**finding, "source_title": "Reddit 1 updated", "engagement_score": 15.0}],
    )

    sightings = store.get_sightings_for_run(topic["id"], run_id)
    assert len(sightings) == 1
    assert sightings[0]["source_title"] == "Reddit 1 updated"
    assert sightings[0]["engagement_score"] == 15.0


def test_get_latest_completed_runs_returns_newest_completed_only(temp_db):
    """Test latest-run lookup ignores failed runs and orders newest first."""
    topic = store.add_topic("Test Topic")
    old_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.record_run(topic["id"], source_mode="v3", status="failed")
    latest_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")

    runs = store.get_latest_completed_runs(topic["id"], limit=2)

    assert [run["id"] for run in runs] == [latest_run_id, old_run_id]


def test_compute_topic_delta_compares_latest_two_runs(temp_db):
    """Test watchlist delta classification using per-run sightings."""
    topic = store.add_topic("Test Topic")
    previous_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(previous_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/continued",
            "source_title": "Continued",
            "content": "Still present",
            "engagement_score": 10.0,
            "relevance_score": 0.7,
        },
        {
            "source": "x",
            "source_url": "https://x.com/dropped/status/1",
            "source_title": "Dropped",
            "content": "Dropped this run",
            "engagement_score": 20.0,
            "relevance_score": 0.8,
        },
    ])
    current_run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(current_run_id, topic["id"], [
        {
            "source": "reddit",
            "source_url": "https://reddit.com/continued",
            "source_title": "Continued",
            "content": "Still present",
            "engagement_score": 15.0,
            "relevance_score": 0.7,
        },
        {
            "source": "github",
            "source_url": "https://github.com/example/new",
            "source_title": "New",
            "content": "New this run",
            "engagement_score": 30.0,
            "relevance_score": 0.9,
        },
    ])

    delta = store.compute_topic_delta(topic["id"])

    assert delta["status"] == "ok"
    assert delta["current_run_id"] == current_run_id
    assert delta["previous_run_id"] == previous_run_id
    assert delta["new"] == 1
    assert delta["continued"] == 1
    assert delta["dropped"] == 1
    assert [f["source_url"] for f in delta["findings"]["new"]] == ["https://github.com/example/new"]
    assert [f["source_url"] for f in delta["findings"]["continued"]] == ["https://reddit.com/continued"]
    assert [f["source_url"] for f in delta["findings"]["dropped"]] == ["https://x.com/dropped/status/1"]
    assert delta["sources"] == {
        "github": {"new": 1, "continued": 0, "dropped": 0},
        "reddit": {"new": 0, "continued": 1, "dropped": 0},
        "x": {"new": 0, "continued": 0, "dropped": 1},
    }


def test_compute_topic_delta_requires_two_completed_runs(temp_db):
    """Test that delta reports insufficient history before two successful runs."""
    topic = store.add_topic("Test Topic")
    store.record_run(topic["id"], source_mode="v3", status="completed")

    delta = store.compute_topic_delta(topic["id"])

    assert delta["status"] == "insufficient_history"
    assert "Need at least two completed runs" in delta["message"]


def test_update_validates_allowed_columns(temp_db, sample_report):
    """Test update_run/update_finding accept valid keys and reject invalid keys."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")

    # Valid run update key should not raise
    store.update_run(run_id, status="failed")

    findings = store.findings_from_report(sample_report)
    store.store_findings(run_id, topic["id"], findings[:1])

    conn = sqlite3.connect(str(temp_db))
    finding_id = conn.execute("SELECT id FROM findings LIMIT 1").fetchone()[0]
    conn.close()

    # Valid finding update key should not raise
    store.update_finding(finding_id, dismissed=1)

    with pytest.raises(ValueError, match="invalid_run_column"):
        store.update_run(run_id, invalid_run_column="x")

    with pytest.raises(ValueError, match="invalid_finding_column"):
        store.update_finding(finding_id, invalid_finding_column="x")

# === Tests for topic management ===


def test_add_topic(temp_db):
    """Test adding a topic."""
    topic = store.add_topic("Test Topic", schedule="0 8 * * *")
    
    assert topic["name"] == "Test Topic"
    assert topic["schedule"] == "0 8 * * *"
    assert topic["enabled"] == 1


def test_add_topic_with_search_queries(temp_db):
    """Test adding a topic with custom search queries."""
    topic = store.add_topic(
        "Test Topic",
        search_queries=["query1", "query2"],
        schedule="0 8 * * *",
    )
    
    assert topic["name"] == "Test Topic"
    assert json.loads(topic["search_queries"]) == ["query1", "query2"]


def test_remove_topic_cascades_findings(temp_db, sample_report):
    """Test that removing a topic deletes its findings and runs."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    findings = store.findings_from_report(sample_report)
    store.store_findings(run_id, topic["id"], findings)
    
    # Verify data exists
    conn = sqlite3.connect(str(temp_db))
    finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    run_count = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert finding_count == 4
    assert run_count == 1
    conn.close()
    
    # Remove topic
    removed = store.remove_topic("Test Topic")
    assert removed is True
    
    # Verify cascade delete
    conn = sqlite3.connect(str(temp_db))
    finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    run_count = conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert finding_count == 0
    assert run_count == 0
    conn.close()


def test_list_topics(temp_db):
    """Test listing topics with stats."""
    # Add multiple topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    store.add_topic("Topic 3")
    
    topics = store.list_topics()
    
    assert len(topics) == 3
    assert {t["name"] for t in topics} == {"Topic 1", "Topic 2", "Topic 3"}
    
    # Check that stats fields are present
    for topic in topics:
        assert "finding_count" in topic
        assert "last_run" in topic
        assert "last_status" in topic

# === Tests for get_new_findings() ===


def test_get_new_findings(temp_db, sample_report):
    """Test retrieving new findings for a topic."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    findings = store.findings_from_report(sample_report)
    store.store_findings(run_id, topic["id"], findings)
    
    new_findings = store.get_new_findings(topic["id"])
    
    assert len(new_findings) == 4
    sources = {f["source"] for f in new_findings}
    assert "hackernews" in sources
    assert "polymarket" in sources


def test_get_new_findings_filters_by_date(temp_db, sample_report):
    """Test that since parameter filters findings correctly."""
    topic = store.add_topic("Test Topic")
    run_id = store.record_run(topic["id"], source_mode="v3")
    findings = store.findings_from_report(sample_report)
    store.store_findings(run_id, topic["id"], findings)
    
    # Use UTC because store writes first_seen via SQLite's datetime('now') (UTC).
    # Local-time math here would flake near midnight UTC.
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    new_findings = store.get_new_findings(topic["id"], since=tomorrow)

    assert len(new_findings) == 0

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    new_findings = store.get_new_findings(topic["id"], since=yesterday)

    assert len(new_findings) == 4

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
