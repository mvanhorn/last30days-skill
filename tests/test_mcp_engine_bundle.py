"""Exercise the Python engine layout produced for the MCP binary."""

import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_synced_engine_persists_research_and_emits_json(tmp_path):
    staged_repo = tmp_path / "bundle source"
    scripts = staged_repo / "skills" / "last30days" / "scripts"
    shutil.copytree(
        ROOT / "skills" / "last30days" / "scripts",
        scripts,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    sync_script = staged_repo / "mcp" / "scripts" / "sync-engine.sh"
    sync_script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "mcp" / "scripts" / "sync-engine.sh", sync_script)
    subprocess.run(
        ["bash", str(sync_script)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    engine = staged_repo / "mcp" / "internal" / "engine" / "vendored"
    save_dir = tmp_path / "saved research"
    topic = "MCP persistence regression"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(engine / "last30days.py"),
            topic,
            "--mock",
            "--quick",
            "--search=reddit",
            "--no-browser-cookies",
            "--emit=json",
            "--save-dir",
            str(save_dir),
        ],
        cwd=tmp_path,
        env={
            "PATH": str(tmp_path / "empty-bin"),
            "LAST30DAYS_CONFIG_DIR": str(tmp_path / "config"),
            "LAST30DAYS_CACHE_DIR": str(tmp_path / "cache"),
            "LAST30DAYS_SKIP_KEYCHAIN": "1",
            "LAST30DAYS_STORE": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["query"] == topic
    db_path = save_dir / "research.db"
    assert db_path.is_file()
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT name FROM topics").fetchall() == [(topic,)]
        assert conn.execute("SELECT status FROM research_runs").fetchall() == [("completed",)]
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] > 0
