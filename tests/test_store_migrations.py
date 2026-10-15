"""Migrations publish schema changes and their version marker together."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import store


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
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 4
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
        assert [row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY version")] == [1, 2, 3, 4]
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
        assert reader.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 4
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

        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 4
        assert conn.execute("SELECT value FROM settings WHERE key = 'daily_budget'").fetchone()[0] == "9.25"
        assert "cost_unknown" in {row[1] for row in conn.execute("PRAGMA table_info(research_runs)")}
    finally:
        conn.close()


def test_migration_retains_trigger_body_and_quoted_semicolons(version_three, monkeypatch):
    monkeypatch.setattr(store, "MIGRATIONS", {
        **store.MIGRATIONS,
        5: """
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
