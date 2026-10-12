"""Shared findings retain every topic's membership and history."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest

import briefing
import store


@pytest.fixture
def shared_findings(tmp_path):
    path = tmp_path / "research.db"
    with store.scoped_db(path):
        store.init_db()
        topics = {name: store.add_topic(name) for name in ("A", "B")}
        runs = {}
        for name in topics:
            runs[name] = store.record_run(topics[name]["id"])
            store.store_findings(runs[name], topics[name]["id"], [
                {
                    "source": "reddit",
                    "source_url": "https://example.test/shared",
                    "source_title": "Shared research",
                    "engagement_score": 10,
                },
                {
                    "source": "reddit",
                    "source_url": f"https://example.test/{name}",
                    "source_title": f"Exclusive {name}",
                    "engagement_score": 5,
                },
            ])
        yield path, topics, runs


@pytest.fixture
def later_topic_sighting(tmp_path, monkeypatch):
    path = tmp_path / "research.db"
    monkeypatch.setattr(briefing, "BRIEFS_DIR", tmp_path / "briefs")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    recent = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    finding = {
        "source": "reddit", "source_url": "https://example.test/shared",
        "source_title": "Shared research", "engagement_score": 10,
    }
    with store.scoped_db(path):
        topics = {name: store.add_topic(name) for name in ("A", "B")}
        for name, seen in (("A", old), ("B", recent)):
            run = store.record_run(topics[name]["id"])
            store.store_findings(run, topics[name]["id"], [finding])
            with sqlite3.connect(path) as conn:
                conn.execute("UPDATE research_runs SET run_date = ? WHERE id = ?", (seen, run))
                conn.execute("UPDATE finding_sightings SET seen_at = ? WHERE run_id = ?", (seen, run))
                conn.execute("UPDATE findings SET first_seen = ?", (old,))
        for name in ("A", "B"):
            run = store.record_run(topics[name]["id"])
            store.store_findings(run, topics[name]["id"], [finding])
        yield path, topics, old, recent


def _create_legacy_findings(path):
    old = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(path) as conn:
        conn.executescript(store.SCHEMA_V1)
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", store._DEFAULT_SETTINGS.items())
        conn.execute("INSERT INTO topics (id, name) VALUES (1, 'A')")
        conn.execute("INSERT INTO research_runs (id, topic_id, run_date) VALUES (1, 1, ?)", (old,))
        for finding_id in (1, 2):
            conn.execute(
                """INSERT INTO findings
                   (id, topic_id, run_id, source, source_url, source_title, first_seen)
                   VALUES (?, 1, 1, 'reddit', ?, 'Legacy research', ?)""",
                (finding_id, f"https://example.test/legacy/{finding_id}", old),
            )
    return old


@pytest.mark.parametrize("resighted_before_upgrade", [False, True])
def test_backfill_preserves_legacy_date_when_same_topic_resights(tmp_path, resighted_before_upgrade):
    path = tmp_path / "legacy.db"
    old = _create_legacy_findings(path)
    finding = {"source": "reddit", "source_url": "https://example.test/legacy/1"}
    with store.scoped_db(path):
        if resighted_before_upgrade:
            with sqlite3.connect(path) as conn:
                for version in sorted(store.MIGRATIONS):
                    conn.executescript(store.MIGRATIONS[version])
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            run = store.record_run(1)
            store.store_findings(run, 1, [finding])
        store.init_db()
        if not resighted_before_upgrade:
            run = store.record_run(1)
            store.store_findings(run, 1, [finding])
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        assert store.get_new_findings(1, since) == []
        assert {item["first_seen"] for item in store.get_new_findings(1)} == {old}
        assert len(store.get_sightings_for_run(1, run)) == 1
        assert store.get_sightings_for_run(1, 1) == []
        assert store.get_trending()[0]["new_findings"] == 0


def test_backfill_does_not_reattribute_old_date_after_owner_removal(tmp_path):
    path = tmp_path / "legacy.db"
    old = _create_legacy_findings(path)
    with store.scoped_db(path):
        store.init_db()
        topic_b = store.add_topic("B")
        run_b = store.record_run(topic_b["id"])
        store.store_findings(run_b, topic_b["id"], [{
            "source": "reddit", "source_url": "https://example.test/legacy/1",
        }])
        recent = store.get_sightings_for_run(topic_b["id"], run_b)[0]["seen_at"]

        assert store.remove_topic("A")
        store.init_db()
        store.init_db()

        findings = store.get_new_findings(topic_b["id"], since=recent)
        assert len(findings) == 1
        assert findings[0]["first_seen"] == recent
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT first_seen FROM findings").fetchone()[0] == old
            assert conn.execute("SELECT COUNT(*) FROM finding_sightings").fetchone()[0] == 1


def test_backfill_rolls_back_historical_sightings_and_marker_on_failure(tmp_path):
    path = tmp_path / "legacy.db"
    _create_legacy_findings(path)
    with sqlite3.connect(path) as conn:
        for version in sorted(store.MIGRATIONS):
            conn.executescript(store.MIGRATIONS[version])
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        before_settings = conn.execute("SELECT * FROM settings ORDER BY key").fetchall()
        conn.execute("""CREATE TRIGGER reject_backfill BEFORE INSERT ON settings
                        WHEN NEW.key = '_topic_sightings_backfilled_v1'
                        BEGIN SELECT RAISE(ABORT, 'backfill blocked'); END""")
    with store.scoped_db(path):
        with pytest.raises(sqlite3.IntegrityError, match="backfill blocked"):
            store.init_db()

        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM finding_sightings").fetchone()[0] == 0
            assert conn.execute("SELECT * FROM settings ORDER BY key").fetchall() == before_settings
            conn.execute("DROP TRIGGER reject_backfill")
        store.init_db()
        store.init_db()
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM finding_sightings").fetchone()[0] == 2
            assert conn.execute(
                "SELECT value FROM settings WHERE key = '_topic_sightings_backfilled_v1'"
            ).fetchone()[0] == "1"


def test_completed_backfill_reads_marker_without_writer_transaction(tmp_path):
    with store.scoped_db(tmp_path / "research.db"):
        store.init_db()
        writer = store._connect()
        reader = store._connect()
        statements = []
        reader.set_trace_callback(statements.append)
        try:
            writer.execute("BEGIN IMMEDIATE")

            store._backfill_owner_sightings(reader)

            assert statements
            assert not any(statement.lstrip().upper().startswith((
                "BEGIN", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
            )) for statement in statements)
        finally:
            writer.rollback()
            writer.close()
            reader.close()


@pytest.mark.parametrize("read", ["list_topics", "get_topic"])
def test_topic_reads_succeed_while_another_connection_holds_writer_lock(tmp_path, read):
    with store.scoped_db(tmp_path / "research.db"):
        store.add_topic("A")
        writer = store._connect()
        try:
            writer.execute("BEGIN IMMEDIATE")

            if read == "list_topics":
                assert [topic["name"] for topic in store.list_topics()] == ["A"]
            else:
                assert store.get_topic("A")["name"] == "A"

            assert writer.in_transaction
        finally:
            writer.rollback()
            writer.close()


def test_initialization_repairs_missing_defaults_without_overwriting_settings(tmp_path):
    path = tmp_path / "research.db"
    with store.scoped_db(path):
        store.init_db()
        store.set_setting("delivery_channel", "existing-channel")
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM settings WHERE key = 'delivery_mode'")
            conn.execute("DELETE FROM schema_version WHERE version = 1")

        store.init_db()

        with sqlite3.connect(path) as conn:
            assert conn.execute(
                "SELECT value FROM settings WHERE key = 'delivery_mode'"
            ).fetchone()[0] == "announce"
            assert conn.execute(
                "SELECT value FROM settings WHERE key = 'delivery_channel'"
            ).fetchone()[0] == "existing-channel"
            assert conn.execute("SELECT version FROM schema_version WHERE version = 1").fetchone() == (1,)


def test_backfill_rechecks_marker_after_waiting_for_another_initializer(tmp_path):
    with store.scoped_db(tmp_path / "research.db"):
        store.init_db()
        writer = store._connect()
        try:
            writer.execute("DELETE FROM settings WHERE key = '_topic_sightings_backfilled_v1'")
            writer.commit()
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "INSERT INTO settings (key, value) VALUES ('_topic_sightings_backfilled_v1', '1')"
            )
            waiting_for_writer = Event()

            def initialize():
                reader = store._connect()
                try:
                    reader.set_trace_callback(lambda sql: (
                        waiting_for_writer.set() if sql == "BEGIN IMMEDIATE" else None
                    ))
                    store._backfill_owner_sightings(reader)
                finally:
                    reader.close()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(initialize)
                try:
                    assert waiting_for_writer.wait(timeout=2)
                finally:
                    writer.commit()
                future.result(timeout=2)
            assert writer.execute(
                "SELECT COUNT(*) FROM settings WHERE key = '_topic_sightings_backfilled_v1'"
            ).fetchone()[0] == 1
        finally:
            writer.close()


@pytest.mark.parametrize("surface", ["findings", "trending", "daily", "weekly"])
def test_new_finding_window_uses_each_topics_first_sighting(later_topic_sighting, surface):
    _, topics, old, recent = later_topic_sighting
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    if surface == "findings":
        assert store.get_new_findings(topics["A"]["id"], since) == []
        findings = store.get_new_findings(topics["B"]["id"], since)
        assert len(findings) == 1
        assert findings[0]["first_seen"] == recent
    elif surface == "trending":
        trends = {item["name"]: item for item in store.get_trending()}
        assert trends["A"]["new_findings"] == 0
        assert trends["B"]["new_findings"] == 1
        assert trends["B"]["total_engagement"] == 10
    elif surface == "daily":
        daily = briefing.generate_daily(since=since)
        assert daily["total_new"] == 1
        assert {item["name"]: item["new_count"] for item in daily["topics"]} == {"A": 0, "B": 1}
    else:
        weekly = {item["name"]: item for item in briefing.generate_weekly()["topics"]}
        assert (weekly["A"]["this_week_count"], weekly["A"]["last_week_count"]) == (0, 1)
        assert (weekly["B"]["this_week_count"], weekly["B"]["last_week_count"]) == (1, 0)


def test_removing_first_owner_preserves_survivors_first_sighting(later_topic_sighting):
    path, topics, old, recent = later_topic_sighting
    assert store.remove_topic("A")

    findings = store.get_new_findings(topics["B"]["id"], since=recent)

    assert len(findings) == 1
    assert findings[0]["first_seen"] == recent
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT first_seen FROM findings").fetchone()[0] == old


@pytest.mark.parametrize("removed", ["A", "B"])
def test_remove_topic_preserves_other_topic_history(shared_findings, removed):
    path, topics, runs = shared_findings
    survivor = "B" if removed == "A" else "A"
    sightings = store.get_sightings_for_run(topics[survivor]["id"], runs[survivor])

    assert store.remove_topic(removed)

    assert store.get_sightings_for_run(topics[survivor]["id"], runs[survivor]) == sightings
    findings = store.get_new_findings(topics[survivor]["id"])
    assert {item["source_url"] for item in findings} == {
        "https://example.test/shared", f"https://example.test/{survivor}",
    }
    shared = next(item for item in findings if item["source_title"] == "Shared research")
    assert shared["run_id"] == runs[survivor]
    assert shared["sighting_count"] == 2
    assert store.search_findings("Shared")[0]["id"] == shared["id"]
    assert store.get_topic(removed) is None
    assert not store.remove_topic(removed)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT id FROM research_runs").fetchall() == [(runs[survivor],)]

    assert store.remove_topic(survivor)
    assert store.get_stats()["total_findings"] == 0


def test_topic_reads_include_shared_findings_once(shared_findings):
    _, topics, _ = shared_findings
    run = store.record_run(topics["B"]["id"])
    store.store_findings(run, topics["B"]["id"], [{
        "source": "reddit", "source_url": "https://example.test/shared",
        "engagement_score": 10,
    }])

    assert {t["name"]: t["finding_count"] for t in store.list_topics()} == {"A": 2, "B": 2}
    for topic in topics.values():
        assert len(store.get_new_findings(topic["id"])) == 2
    trending = {item["name"]: item for item in store.get_trending()}
    assert {name: item["new_findings"] for name, item in trending.items()} == {"A": 2, "B": 2}
    assert {name: item["total_engagement"] for name, item in trending.items()} == {"A": 15, "B": 15}


def test_removal_preserves_membership_between_first_and_latest_topic(shared_findings):
    _, topics, _ = shared_findings
    topic_c = store.add_topic("C")
    run_c = store.record_run(topic_c["id"])
    store.store_findings(run_c, topic_c["id"], [{
        "source": "reddit", "source_url": "https://example.test/shared",
    }])

    assert store.remove_topic("A")

    assert len(store.get_new_findings(topics["B"]["id"])) == 2
    shared = store.get_new_findings(topic_c["id"])
    assert len(shared) == 1
    assert shared[0]["source_url"] == "https://example.test/shared"
    assert shared[0]["run_id"] == run_c
    assert store.remove_topic("C")
    assert len(store.get_new_findings(topics["B"]["id"])) == 2


def test_removal_preserves_other_topic_delta(shared_findings):
    _, topics, _ = shared_findings
    run = store.record_run(topics["B"]["id"])
    store.store_findings(run, topics["B"]["id"], [{
        "source": "reddit", "source_url": "https://example.test/shared",
    }])
    before = store.compute_topic_delta(topics["B"]["id"])
    assert before["continued"] == 1
    assert before["dropped"] == 1

    assert store.remove_topic("A")

    assert store.compute_topic_delta(topics["B"]["id"]) == before


def test_weekly_briefing_counts_shared_findings_in_both_periods(shared_findings, monkeypatch):
    path, _, _ = shared_findings
    monkeypatch.setattr(briefing, "BRIEFS_DIR", path.parent / "briefs")
    last_week = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE findings SET first_seen = ? WHERE source_url = ?",
            (last_week, "https://example.test/shared"),
        )
        conn.execute(
            "UPDATE finding_sightings SET seen_at = ? WHERE source_url = ?",
            (last_week, "https://example.test/shared"),
        )

    result = briefing.generate_weekly()

    assert result["status"] == "ok"
    assert len(result["topics"]) == 2
    for topic in result["topics"]:
        assert topic["this_week_count"] == 1
        assert topic["last_week_count"] == 1
        assert topic["engagement_change_pct"] == -50.0


def test_topic_findings_date_range_excludes_upper_boundary(shared_findings):
    path, topics, _ = shared_findings
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE findings SET first_seen = '2026-01-01'")
        conn.execute("UPDATE finding_sightings SET seen_at = '2026-01-01'")
        conn.execute(
            "UPDATE findings SET first_seen = '2026-01-08' WHERE source_url = ?",
            ("https://example.test/shared",),
        )
        conn.execute(
            "UPDATE finding_sightings SET seen_at = '2026-01-08' WHERE source_url = ?",
            ("https://example.test/shared",),
        )

    findings = store.get_new_findings(topics["B"]["id"], "2026-01-01", before="2026-01-08")

    assert [item["source_url"] for item in findings] == ["https://example.test/B"]
    assert store.get_new_findings(topics["B"]["id"], before="2026-01-01") == []


@pytest.mark.parametrize("removed", ["A", "B"])
def test_remove_topic_preserves_legacy_aggregate_membership(shared_findings, removed):
    path, topics, runs = shared_findings
    survivor = "B" if removed == "A" else "A"
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM finding_sightings")

    assert store.remove_topic(removed)

    findings = store.get_new_findings(topics[survivor]["id"])
    assert {item["source_url"] for item in findings} == {
        "https://example.test/shared", f"https://example.test/{survivor}",
    }
    shared = next(item for item in findings if item["source_title"] == "Shared research")
    assert shared["run_id"] == (runs["B"] if survivor == "B" else None)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_remove_topic_rolls_back_when_deletion_fails(shared_findings):
    path, topics, runs = shared_findings
    with sqlite3.connect(path) as conn:
        before = conn.execute("SELECT * FROM findings ORDER BY id").fetchall()
        conn.execute("""CREATE TRIGGER reject_topic_delete BEFORE DELETE ON topics
                        BEGIN SELECT RAISE(ABORT, 'deletion blocked'); END""")

    with pytest.raises(sqlite3.IntegrityError, match="deletion blocked"):
        store.remove_topic("A")

    assert store.get_topic("A") == topics["A"]
    assert len(store.get_sightings_for_run(topics["A"]["id"], runs["A"])) == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM findings ORDER BY id").fetchall() == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
