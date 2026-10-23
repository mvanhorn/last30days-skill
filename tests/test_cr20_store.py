"""Regression coverage for concurrent engagement updates in the research store."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import store


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", path)
    store.init_db()
    topic = store.add_topic("Concurrent research")
    return path, topic["id"]


@pytest.mark.parametrize("existing", [True, False], ids=["existing-row", "insert-conflict"])
def test_concurrent_observations_keep_maximum_and_each_run_history(database, monkeypatch, existing):
    path, topic_id = database
    finding = {
        "source": "reddit",
        "source_url": "https://reddit.com/r/test/concurrent",
        "source_title": "Concurrent observation",
    }
    expected_history = []
    expected_counts = []
    if existing:
        initial_run = store.record_run(topic_id)
        assert store.store_findings(initial_run, topic_id, [{**finding, "engagement_score": 10}]) == {
            "new": 1, "updated": 0,
        }
        expected_history.append((initial_run, 10.0))
        expected_counts.append((initial_run, 1, 0))
    runs = {score: store.record_run(topic_id) for score in (100, 50)}

    read_barrier = threading.Barrier(2, timeout=5)
    high_committed = threading.Event()
    worker = threading.local()
    observed = {}
    real_connect = store._connect

    class GatedCursor:
        def __init__(self, cursor, score):
            self.cursor = cursor
            self.score = score

        def fetchall(self):
            rows = self.cursor.fetchall()
            observed[self.score] = [row["engagement_score"] for row in rows]
            read_barrier.wait()
            if self.score == 50:
                assert high_committed.wait(timeout=5), "The high observation did not commit"
            return rows

    class GatedConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            if sql.startswith("SELECT id, source_url, engagement_score FROM findings WHERE source_url IN"):
                return GatedCursor(cursor, worker.score)
            return cursor

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(store, "_connect", lambda: GatedConnection(real_connect()))

    def observe(score):
        worker.score = score
        result = store.store_findings(runs[score], topic_id, [{**finding, "engagement_score": score}])
        if score == 100:
            high_committed.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {score: executor.submit(observe, score) for score in (100, 50)}
        results = {score: future.result(timeout=10) for score, future in futures.items()}

    assert observed == {100: [10.0] if existing else [], 50: [10.0] if existing else []}
    assert results == {
        100: {"new": 0 if existing else 1, "updated": 1 if existing else 0},
        50: {"new": 0, "updated": 1},
    }
    expected_history.extend([(runs[100], 100.0), (runs[50], 50.0)])
    expected_counts.extend([
        (runs[100], 0 if existing else 1, 1 if existing else 0),
        (runs[50], 0, 1),
    ])
    with sqlite3.connect(path) as connection:
        aggregate = connection.execute(
            "SELECT engagement_score, sighting_count, run_id FROM findings"
        ).fetchall()
        history = connection.execute(
            "SELECT run_id, engagement_score FROM finding_sightings ORDER BY run_id"
        ).fetchall()
        counts = connection.execute(
            "SELECT id, findings_new, findings_updated FROM research_runs ORDER BY id"
        ).fetchall()
    assert aggregate == [(100.0, 3 if existing else 2, runs[50])]
    assert history == expected_history
    assert counts == expected_counts


@pytest.mark.parametrize(
    "stored,incoming,expected",
    [(10, 50, 50), (100, 50, 100), (None, 50, 50), (100, None, 100), (None, None, 0)],
)
def test_existing_engagement_preserves_maximum_and_handles_null(database, stored, incoming, expected):
    path, topic_id = database
    finding = {"source": "reddit", "source_url": "https://reddit.com/r/test/sequential"}
    initial_run = store.record_run(topic_id)
    store.store_findings(initial_run, topic_id, [{**finding, "engagement_score": stored}])
    if stored is None:
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE findings SET engagement_score = NULL")
    run = store.record_run(topic_id)
    assert store.store_findings(run, topic_id, [{**finding, "engagement_score": incoming}]) == {
        "new": 0, "updated": 1,
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT engagement_score, sighting_count FROM findings").fetchall() == [
            (expected, 2),
        ]
        assert connection.execute(
            "SELECT engagement_score FROM finding_sightings WHERE run_id = ?", (run,)
        ).fetchone() == (incoming or 0,)
