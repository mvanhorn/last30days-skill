"""Tests for store.py - SQLite research accumulator and watchlist storage."""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

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
        "ranked_candidates": [],
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
            "reddit": [
                {
                    "item_id": "R1",
                    "source": "reddit",
                    "title": "Test",
                    "body": "Content",
                    "url": "https://reddit.com/1",
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
    
    # Get findings since tomorrow (should be empty)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    new_findings = store.get_new_findings(topic["id"], since=tomorrow)
    
    assert len(new_findings) == 0
    
    # Get findings since yesterday (should have all)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    new_findings = store.get_new_findings(topic["id"], since=yesterday)
    
    assert len(new_findings) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
