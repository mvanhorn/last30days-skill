"""Migrations publish schema changes and their version marker together."""

import datetime as dt
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import store
from lib import forecast


@pytest.fixture
def version_three(tmp_path, monkeypatch):
    path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", path)
    migrations = store.MIGRATIONS
    monkeypatch.setattr(store, "MIGRATIONS", {key: value for key, value in migrations.items() if key < 4})
    store.init_db()
    monkeypatch.setattr(store, "MIGRATIONS", migrations)
    return path


def test_failed_version_write_rolls_back_schema_and_retry_succeeds(version_three):
    conn = store._connect()

    def deny_version_write(action, table, *unused):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_INSERT and table == "schema_version" else sqlite3.SQLITE_OK

    conn.set_authorizer(deny_version_write)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            store._run_migrations(conn)
    finally:
        conn.close()

    conn = store._connect()
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 3
        assert "cost_unknown" not in {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
    finally:
        conn.close()
    store.init_db()
    conn = store._connect()
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
        assert "cost_unknown" in {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
    finally:
        conn.close()


def test_concurrent_initialization_applies_each_version_once(version_three):
    barrier = threading.Barrier(8)

    def initialize(_):
        barrier.wait(timeout=5)
        return store.init_db()

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(initialize, range(8))) == [version_three] * 8
    conn = store._connect()
    try:
        assert [row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")] == [1, 2, 3, 4, 5, 6]
    finally:
        conn.close()


def test_current_schema_check_succeeds_while_another_connection_holds_writer(version_three):
    store.init_db()
    writer = store._connect()
    reader = store._connect()
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO settings (key, value) VALUES ('held-writer', 'uncommitted')")
        reader.execute("PRAGMA busy_timeout=1")

        store._run_migrations(reader)

        assert not reader.in_transaction
        assert reader.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
        assert reader.execute("SELECT value FROM settings WHERE key = 'held-writer'").fetchone() is None
    finally:
        writer.rollback()
        writer.close()
        reader.close()


def test_pending_migration_after_committed_default_seeding(version_three):
    conn = store._connect()
    try:
        conn.execute("DELETE FROM settings WHERE key = 'daily_budget'")
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", ("daily_budget", "9.25"))
        conn.commit()

        store._run_migrations(conn)

        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
        assert conn.execute("SELECT value FROM settings WHERE key = 'daily_budget'").fetchone()[0] == "9.25"
        assert "cost_unknown" in {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
    finally:
        conn.close()


def test_migration_retains_trigger_body_and_quoted_semicolons(version_three, monkeypatch):
    monkeypatch.setattr(store, "MIGRATIONS", {
        **store.MIGRATIONS,
        max(store.MIGRATIONS) + 1: """
CREATE TABLE migration_probe (value TEXT);
CREATE TRIGGER migration_probe_insert AFTER INSERT ON migration_probe BEGIN
    INSERT INTO settings(key, value) VALUES ('trigger-one', 'value;one');
    INSERT INTO settings(key, value) VALUES ('trigger-two', new.value);
END;
INSERT INTO migration_probe VALUES ('value;two');
""",
    })
    store.init_db()
    assert store.get_setting("trigger-one") == "value;one"
    assert store.get_setting("trigger-two") == "value;two"


@pytest.fixture
def ledger_before_baselines(tmp_path, monkeypatch):
    """A forecast ledger written at schema version 5, before baselines existed."""
    path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", path)
    migrations = store.MIGRATIONS
    monkeypatch.setattr(store, "MIGRATIONS", {key: value for key, value in migrations.items() if key <= 5})
    store.init_db()
    conn = store._connect()
    try:
        conn.execute(
            """INSERT INTO forecasts
               (topic, normalized_topic, claim, normalized_claim, probability, created_on,
                created_at, resolve_by, resolution, basis, market_url, market_outcome, market_price)
               VALUES ('Widget Co', 'widget co', 'Widget Co ships a stable 2.0', 'widget co ships a stable 2 0',
                       0.7, '2026-10-01', '2026-10-01T12:00:00+00:00', '2026-12-09',
                       'A v2.0.0 tag is on the releases page', '["https://example.com/thread/1"]',
                       NULL, NULL, NULL)"""
        )
        conn.execute(
            """INSERT INTO forecasts
               (topic, normalized_topic, claim, normalized_claim, probability, created_on,
                created_at, resolve_by, resolution, basis, verdict, graded_on, graded_by, evidence_url)
               VALUES ('Widget Co', 'widget co', 'Widget Co raises a round', 'widget co raises a round',
                       0.8, '2026-09-01', '2026-09-01T12:00:00+00:00', '2026-10-01',
                       'A funding announcement is published', '[]',
                       'hit', '2026-10-02', 'host', 'https://example.com/round')"""
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(store, "MIGRATIONS", migrations)
    return path


def test_ledger_from_before_baselines_keeps_its_rows_as_legacy_calls(ledger_before_baselines):
    rows = store.list_forecasts()

    assert [(row["id"], row["claim"], row["probability"], row["verdict"]) for row in rows] == [
        (1, "Widget Co ships a stable 2.0", 0.7, None),
        (2, "Widget Co raises a round", 0.8, "hit"),
    ]
    assert rows[0]["basis"] == ["https://example.com/thread/1"]
    assert [row["kind"] for row in rows] == ["legacy", "legacy"]
    assert all(row["question_key"] is None and row["baseline_probability"] is None for row in rows)
    listing = forecast.render_list(rows, dt.date(2026, 11, 9), "Forecast ledger")
    assert listing.count("legacy call, logged before baselines were recorded") == 2
    conn = store._connect()
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
    finally:
        conn.close()


def test_migrated_ledger_allows_one_open_call_per_topic_and_question(ledger_before_baselines):
    store.init_db()
    conn = store._connect()
    insert = """INSERT INTO forecasts
        (topic, normalized_topic, claim, normalized_claim, probability, created_on, created_at,
         resolve_by, resolution, basis, kind, question_key)
        VALUES ('Widget Co', 'widget co', ?, ?, 0.7, '2026-11-09', '2026-11-09T12:00:00+00:00',
                '2026-12-09', 'The market resolves No', '[]', 'question', 'market:widget-co')"""
    try:
        conn.execute(insert, ("First wording of the call", "first wording of the call"))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("Second wording of the call", "second wording of the call"))
        # Once the first call has a verdict, the question can take a new one.
        conn.execute("UPDATE forecasts SET verdict = 'void' WHERE question_key = 'market:widget-co'")
        conn.execute(insert, ("Second wording of the call", "second wording of the call"))
    finally:
        conn.close()
