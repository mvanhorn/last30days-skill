"""Tests for watchlist.py command functions."""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import store
import watchlist
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


# === Tests for cmd_add() ===

def test_cmd_add_basic(temp_db, capsys):
    """Test adding a topic with default schedule."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = False
    args.schedule = None
    args.queries = None
    
    watchlist.cmd_add(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "added"
    assert output["topic"] == "Test Topic"
    assert "daily" in output["schedule"]


def test_cmd_add_with_custom_schedule(temp_db, capsys):
    """Test adding a topic with custom schedule."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = False
    args.schedule = "0 12 * * *"
    args.queries = None
    
    watchlist.cmd_add(args)
    
    # Verify in database
    topic = store.get_topic("Test Topic")
    assert topic["schedule"] == "0 12 * * *"


def test_cmd_add_weekly(temp_db, capsys):
    """Test adding a topic with weekly schedule."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = True
    args.schedule = None
    args.queries = None
    
    watchlist.cmd_add(args)
    
    # Verify weekly schedule
    topic = store.get_topic("Test Topic")
    assert topic["schedule"] == "0 8 * * 1"  # Monday 8am


def test_cmd_add_with_search_queries(temp_db, capsys):
    """Test adding a topic with custom search queries."""
    args = Mock()
    args.topic = "Test Topic"
    args.weekly = False
    args.schedule = None
    args.queries = "query1, query2, query3"
    
    watchlist.cmd_add(args)
    
    # Verify queries stored
    topic = store.get_topic("Test Topic")
    queries = json.loads(topic["search_queries"])
    assert queries == ["query1", "query2", "query3"]


# === Tests for cmd_remove() ===

def test_cmd_remove_existing_topic(temp_db, capsys):
    """Test removing an existing topic."""
    # Add a topic first
    store.add_topic("Test Topic")
    
    args = Mock()
    args.topic = "Test Topic"
    
    watchlist.cmd_remove(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "removed"
    assert output["topic"] == "Test Topic"


def test_cmd_remove_nonexistent_topic(temp_db, capsys):
    """Test removing a topic that doesn't exist."""
    args = Mock()
    args.topic = "Nonexistent Topic"
    
    watchlist.cmd_remove(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "not_found"
    assert output["topic"] == "Nonexistent Topic"


# === Tests for cmd_list() ===

def test_cmd_list_empty(temp_db, capsys):
    """Test listing when no topics exist."""
    args = Mock()
    
    watchlist.cmd_list(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["topics"] == []
    assert output["budget_used"] == 0.0
    assert output["budget_limit"] == 5.0


def test_cmd_list_with_topics(temp_db, capsys):
    """Test listing with multiple topics."""
    # Add topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    store.add_topic("Topic 3")
    
    args = Mock()
    
    watchlist.cmd_list(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert len(output["topics"]) == 3
    topic_names = {t["name"] for t in output["topics"]}
    assert topic_names == {"Topic 1", "Topic 2", "Topic 3"}


# === Tests for cmd_config() ===

def test_cmd_config_delivery(temp_db, capsys):
    """Test configuring delivery channel."""
    args = Mock()
    args.key = "delivery"
    args.value = "https://hooks.slack.com/services/TEST"
    
    watchlist.cmd_config(args)
    
    # Verify setting stored
    channel = store.get_setting("delivery_channel")
    assert channel == "https://hooks.slack.com/services/TEST"
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "config"
    assert output["key"] == "delivery_channel"


def test_cmd_config_budget(temp_db, capsys):
    """Test configuring daily budget."""
    args = Mock()
    args.key = "budget"
    args.value = 10.0
    
    watchlist.cmd_config(args)
    
    # Verify setting stored
    budget = store.get_setting("daily_budget")
    assert budget == "10.0"


def test_cmd_config_unknown_key(temp_db):
    """Test that unknown config key raises error."""
    args = Mock()
    args.key = "unknown_key"
    args.value = "value"
    
    with pytest.raises(SystemExit):
        watchlist.cmd_config(args)


# === Tests for _run_topic() ===

@patch('watchlist.subprocess.run')
def test_run_topic_success(mock_subprocess, temp_db):
    """Test successful topic run."""
    topic = store.add_topic("Test Topic")
    
    # Mock successful subprocess call
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
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
                    "title": "Test",
                    "body": "Content",
                    "url": "https://reddit.com/1",
                    "author": "user",
                    "engagement_score": 50.0,
                    "local_relevance": 0.8,
                    "snippet": "Snippet",
                }
            ],
        },
        "errors_by_source": {},
        "warnings": [],
    })
    mock_subprocess.return_value = mock_result
    
    result = watchlist._run_topic(topic)
    
    assert result["status"] == "completed"
    assert result["new"] == 1
    assert result["topic"] == "Test Topic"


@patch('watchlist.subprocess.run')
def test_run_topic_failure(mock_subprocess, temp_db):
    """Test topic run failure."""
    topic = store.add_topic("Test Topic")
    
    # Mock failed subprocess call
    mock_result = Mock()
    mock_result.returncode = 1
    mock_result.stderr = "Error message"
    mock_subprocess.return_value = mock_result
    
    result = watchlist._run_topic(topic)
    
    assert result["status"] == "failed"
    assert "Error message" in result["error"]


@patch('watchlist.subprocess.run')
def test_run_topic_timeout(mock_subprocess, temp_db):
    """Test topic run timeout."""
    topic = store.add_topic("Test Topic")
    
    # Mock timeout
    mock_subprocess.side_effect = subprocess.TimeoutExpired("cmd", 300)
    
    result = watchlist._run_topic(topic)
    
    assert result["status"] == "failed"
    assert result["error"] == "timeout"


@patch('watchlist.subprocess.run')
@patch('watchlist._deliver_findings')
def test_run_topic_calls_delivery(mock_deliver, mock_subprocess, temp_db):
    """Test that successful run calls delivery."""
    topic = store.add_topic("Test Topic")
    
    # Mock successful subprocess call with findings
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps({
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
                    "title": "Test",
                    "body": "Content",
                    "url": "https://reddit.com/1",
                    "author": "user",
                    "engagement_score": 50.0,
                    "local_relevance": 0.8,
                    "snippet": "Snippet",
                }
            ],
        },
        "errors_by_source": {},
        "warnings": [],
    })
    mock_subprocess.return_value = mock_result
    
    watchlist._run_topic(topic)
    
    # Verify delivery was called
    assert mock_deliver.called
    call_args = mock_deliver.call_args[0]
    assert call_args[0] == "Test Topic"
    assert call_args[1]["new"] == 1


# === Tests for cmd_run_one() ===

@patch('watchlist._run_topic')
def test_cmd_run_one(mock_run, temp_db, capsys):
    """Test running a single topic."""
    topic = store.add_topic("Test Topic")
    
    mock_run.return_value = {
        "topic": "Test Topic",
        "status": "completed",
        "new": 5,
        "updated": 2,
        "duration": 60.0,
    }
    
    args = Mock()
    args.topic = "Test Topic"
    
    watchlist.cmd_run_one(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["status"] == "completed"
    assert output["new"] == 5


def test_cmd_run_one_nonexistent_topic(temp_db, capsys):
    """Test running a nonexistent topic."""
    args = Mock()
    args.topic = "Nonexistent Topic"
    
    with pytest.raises(SystemExit):
        watchlist.cmd_run_one(args)


# === Tests for cmd_run_all() ===

@patch('watchlist._run_topic')
def test_cmd_run_all_no_topics(mock_run, temp_db, capsys):
    """Test running all topics when none exist."""
    args = Mock()
    
    watchlist.cmd_run_all(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert "No enabled topics" in output["message"]


@patch('watchlist._run_topic')
def test_cmd_run_all_multiple_topics(mock_run, temp_db, capsys):
    """Test running multiple topics."""
    # Add topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    
    mock_run.return_value = {
        "topic": "Test",
        "status": "completed",
        "new": 5,
        "updated": 2,
        "duration": 60.0,
    }
    
    args = Mock()
    
    watchlist.cmd_run_all(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    assert output["action"] == "run_all"
    assert len(output["results"]) == 2


@patch('watchlist._run_topic')
@patch('watchlist.store.get_daily_cost')
def test_cmd_run_all_respects_budget(mock_cost, mock_run, temp_db, capsys):
    """Test that run-all respects daily budget."""
    # Add topics
    store.add_topic("Topic 1")
    store.add_topic("Topic 2")
    store.add_topic("Topic 3")
    
    # Mock budget exceeded (budget limit is 5.0)
    mock_cost.return_value = 6.0  # Over budget
    
    mock_run.return_value = {
        "topic": "Test",
        "status": "completed",
        "new": 5,
        "updated": 2,
        "duration": 60.0,
    }
    
    args = Mock()
    
    watchlist.cmd_run_all(args)
    
    # Verify output
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    
    # All topics should be skipped due to budget
    results = output["results"]
    skipped = [r for r in results if r["status"] == "skipped"]
    
    assert len(skipped) == 3  # All 3 topics skipped


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
