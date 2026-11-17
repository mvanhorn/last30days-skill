import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import briefing
import pytest
import store
from lib import library_index


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")


@pytest.fixture
def trend_store(tmp_path, monkeypatch):
    database = tmp_path / "archive #1" / "research.db"
    archive = tmp_path / "briefs"
    monkeypatch.setattr(store, "_db_override", database)
    monkeypatch.setattr(briefing, "BRIEFS_DIR", archive)
    old_umask = os.umask(0o022)
    try:
        topic = store.add_topic("Weather stations")
        run = store.record_run(topic["id"], status="completed")
        store.store_findings(run, topic["id"], [{
            "source": "reddit",
            "source_url": "https://example.invalid/stations",
            "source_title": "Weather stations near the sea",
            "content": "The weather stations measure wind speed.",
        }])
        yield database, archive
    finally:
        os.umask(old_umask)


def _sqlite_files(database):
    return database, Path(f"{database}-wal"), Path(f"{database}-shm")


def test_store_and_live_wal_sidecars_are_owner_only(trend_store):
    database, _ = trend_store
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT name FROM topics").fetchone()[0] == "Weather stations"
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in _sqlite_files(database))


@pytest.mark.parametrize("reader", ["store", "library"])
def test_reopening_existing_store_hardens_live_files_without_losing_data(trend_store, reader):
    database, archive = trend_store
    with closing(store._connect()) as conn:
        assert conn.execute("SELECT count(*) FROM findings").fetchone()[0] == 1
        paths = _sqlite_files(database)
        for path in paths:
            path.chmod(0o644)
        if reader == "store":
            assert store.list_topics()[0]["name"] == "Weather stations"
        else:
            matches = library_index.search(
                "weather stations", db_path=archive / "not-created.db", store_db_path=database
            )
            assert matches[0].headline == "Weather stations near the sea"
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths)


def test_library_search_uses_native_path_for_windows_unc_store(trend_store, monkeypatch):
    database, archive = trend_store
    native_path = PureWindowsPath("//server/share/research.db")

    def connect(path, *, uri=False):
        assert path == str(native_path)
        assert uri is False
        return sqlite3.connect(database)

    monkeypatch.setattr(library_index, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(library_index, "prepare_private_sqlite_files", lambda _path: native_path)
    monkeypatch.setattr(library_index, "sqlite3", SimpleNamespace(
        connect=connect, Row=sqlite3.Row, DatabaseError=sqlite3.DatabaseError
    ))

    matches = library_index.search(
        "weather stations", db_path=archive / "not-created.db", store_db_path=database
    )

    assert [match.headline for match in matches] == ["Weather stations near the sea"]


@pytest.mark.parametrize("weekly", [False, True], ids=["daily", "weekly"])
def test_briefing_archive_is_owner_only_on_create_and_rewrite(trend_store, weekly):
    _, archive = trend_store
    generate = briefing.generate_weekly if weekly else briefing.generate_daily
    result = generate()
    path = next(archive.glob("*.json"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == result

    path.chmod(0o644)
    result = generate()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == result
