"""Offline regressions for credential persistence and browser snapshots."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

import last30days as cli
from lib import chrome_cookies, cookie_extract, env, setup_wizard, sqlite_snapshot


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"


@pytest.fixture
def password_store(tmp_path, monkeypatch):
    store = tmp_path / "dummy password store"
    entry = store / "last30days" / "OPENAI_API_KEY.gpg"
    entry.parent.mkdir(parents=True)
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    calls = tmp_path / "pass-calls.txt"
    binary = binary_dir / "pass"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['DUMMY_PASS_CALLS'], 'a') as f:\n"
        "    f.write(' '.join(args) + '\\n')\n"
        "entry = pathlib.Path(os.environ['PASSWORD_STORE_DIR']) / (args[-1] + '.gpg')\n"
        "if args[0] == 'show':\n"
        "    if os.environ.get('DUMMY_DECRYPT_FAILURE') == '1':\n"
        "        sys.exit(2)\n"
        "    sys.exit(0 if entry.exists() else 1)\n"
        "if args[0] == 'insert':\n"
        "    # pass yesno() accepts non-TTY input even without --force.\n"
        "    entry.write_text(sys.stdin.read())\n"
        "    sys.exit(0)\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PASSWORD_STORE_DIR", str(store))
    monkeypatch.setenv("DUMMY_PASS_CALLS", str(calls))
    return entry, calls


def _run_setup_pass(*args):
    return subprocess.run(
        ["bash", str(SCRIPTS / "setup-pass.sh"), *args, "OPENAI_API_KEY"],
        input="dummy-new-key\n", text=True, capture_output=True, timeout=10,
    )


def test_pass_decryption_failure_cannot_overwrite_existing_entry(password_store, monkeypatch):
    entry, calls = password_store
    entry.write_text("dummy-original-encrypted-value\n")
    monkeypatch.setenv("DUMMY_DECRYPT_FAILURE", "1")

    result = _run_setup_pass()

    assert entry.read_text() == "dummy-original-encrypted-value\n"
    assert "insert" not in (calls.read_text() if calls.exists() else "")
    assert "skipping" in result.stdout


def test_pass_missing_entry_is_inserted_without_force(password_store):
    entry, calls = password_store
    result = _run_setup_pass()
    assert result.returncode == 0, result.stderr
    assert entry.read_text() == "dummy-new-key\n"
    assert "added=1 replaced=0" in result.stdout
    assert " -f " not in calls.read_text()


def test_pass_existing_entry_is_skipped_without_replace(password_store):
    entry, calls = password_store
    entry.write_text("dummy-original-encrypted-value\n")
    result = _run_setup_pass()
    assert result.returncode == 0, result.stderr
    assert entry.read_text() == "dummy-original-encrypted-value\n"
    assert "insert" not in (calls.read_text() if calls.exists() else "")


def test_pass_explicit_replace_updates_existing_entry(password_store):
    entry, calls = password_store
    entry.write_text("dummy-original-encrypted-value\n")
    result = _run_setup_pass("--replace")
    assert result.returncode == 0, result.stderr
    assert entry.read_text() == "dummy-new-key\n"
    assert "added=0 replaced=1" in result.stdout
    assert "insert -m -f last30days/OPENAI_API_KEY" in calls.read_text()


def test_pass_entry_created_while_prompting_is_not_overwritten(password_store):
    entry, calls = password_store
    process = subprocess.Popen(
        ["bash", str(SCRIPTS / "setup-pass.sh"), "OPENAI_API_KEY"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.read(31) == f"  {'OPENAI_API_KEY':<28} "
        entry.write_text("dummy-concurrently-created-key\n")
        stdout, stderr = process.communicate("dummy-new-key\n", timeout=10)
        assert process.returncode == 0, stderr
        assert entry.read_text() == "dummy-concurrently-created-key\n"
        assert "insert" not in (calls.read_text() if calls.exists() else "")
        assert "skipping" in stdout
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


@pytest.mark.parametrize("assignment", [
    "SCRAPECREATORS_API_KEY=",
    'SCRAPECREATORS_API_KEY=""',
    "export SCRAPECREATORS_API_KEY='' # awaiting signup",
    "SCRAPECREATORS_API_KEY=   # awaiting signup",
])
def test_signup_persists_usable_key_over_empty_assignment(tmp_path, monkeypatch, capsys, assignment):
    env_path = tmp_path / ".env"
    env_path.write_text(assignment + "\nSETUP_COMPLETE=true\n")
    monkeypatch.setattr(env, "CONFIG_FILE", env_path)
    monkeypatch.setattr(sys, "argv", ["last30days.py", "setup", "--github"])
    monkeypatch.setattr(setup_wizard, "run_github_auth", lambda: {
        "status": "success", "api_key": "dummy-signup-key-0123456789",
    })

    assert cli.main() == 0

    output = capsys.readouterr()
    assert env.load_env_file(env_path)["SCRAPECREATORS_API_KEY"] == "dummy-signup-key-0123456789"
    assert '"persisted": true' in output.out
    assert "dummy-signup-key-0123456789" not in output.out + output.err
    assert env.load_env_file(env_path)["SETUP_COMPLETE"] == "true"
    assert env_path.read_text().count("SCRAPECREATORS_API_KEY") == 1
    if os.name != "nt":
        assert env_path.stat().st_mode & 0o777 == 0o600


def test_signup_writer_preserves_nonempty_duplicate_assignment(tmp_path):
    env_path = tmp_path / ".env"
    original = 'SCRAPECREATORS_API_KEY=""\nSCRAPECREATORS_API_KEY=dummy-user-key\n'
    env_path.write_text(original)
    assert setup_wizard.write_api_key(env_path, "dummy-signup-key")
    assert env_path.read_text() == original
    assert env.load_env_file(env_path)["SCRAPECREATORS_API_KEY"] == "dummy-user-key"


@pytest.mark.parametrize("browser", ["firefox", "chrome"])
def test_browser_snapshot_reads_committed_wal_without_changing_database(tmp_path, browser):
    database = tmp_path / "cookie database #1.sqlite"
    table = "moz_cookies" if browser == "firefox" else "cookies"
    host = "host" if browser == "firefox" else "host_key"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute(
            f"CREATE TABLE {table} (name TEXT, value TEXT, {host} TEXT, encrypted_value BLOB)"
        )
        connection.execute(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
            ("auth_token", "dummy-old-cookie", ".x.com", b""),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute(f"UPDATE {table} SET value = ?", ("dummy-new-cookie",))
        connection.commit()
        connection.execute(f"UPDATE {table} SET value = ?", ("dummy-uncommitted-cookie",))
        wal = database.with_name(database.name + "-wal")
        assert wal.stat().st_size > 0
        before_database, before_wal = database.read_bytes(), wal.read_bytes()

        if browser == "firefox":
            result = cookie_extract._query_cookies_db(database, ".x.com", ["auth_token"])
        else:
            result = chrome_cookies._extract_chromium_cookies_macos(
                database, "Dummy Safe Storage", ".x.com", ["auth_token"]
            )

        assert result == {"auth_token": "dummy-new-cookie"}
        assert database.read_bytes() == before_database
        assert wal.read_bytes() == before_wal
    finally:
        connection.close()


@pytest.mark.parametrize("browser", ["firefox", "chrome"])
def test_locked_browser_snapshot_times_out_and_removes_private_copy(tmp_path, monkeypatch, browser):
    database = tmp_path / "locked.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE locked (value TEXT)")
    connection.commit()
    connection.execute("BEGIN EXCLUSIVE")
    copies = []

    def bounded_snapshot(source, destination):
        copies.append(Path(destination))
        sqlite_snapshot.copy_snapshot(source, destination, timeout=0.05)

    module = cookie_extract if browser == "firefox" else chrome_cookies
    monkeypatch.setattr(module, "copy_snapshot", bounded_snapshot)
    started = time.monotonic()
    try:
        if browser == "firefox":
            result = cookie_extract._query_cookies_db(database, ".x.com", ["auth_token"])
        else:
            result = chrome_cookies._extract_chromium_cookies_macos(
                database, "Dummy Safe Storage", ".x.com", ["auth_token"]
            )
        assert result is None
        assert time.monotonic() - started < 1
        assert len(copies) == 1
        assert not copies[0].exists()
    finally:
        connection.close()


def test_snapshot_preserves_source_permission_error(tmp_path, monkeypatch):
    database = tmp_path / "denied.sqlite"
    real_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == database:
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(PermissionError) as error:
        sqlite_snapshot.copy_snapshot(database, str(tmp_path / "copy.sqlite"))
    assert error.value.filename == str(database)


@pytest.mark.parametrize("failure", ["missing_table", "malformed_cookie"])
def test_chromium_snapshot_query_failure_removes_wal_sidecars(tmp_path, monkeypatch, failure):
    database = tmp_path / "Cookies"
    copies = tmp_path / "private-copies"
    copies.mkdir()
    real_mkstemp = chrome_cookies.tempfile.mkstemp
    monkeypatch.setattr(
        chrome_cookies.tempfile, "mkstemp",
        lambda **kwargs: real_mkstemp(dir=copies, **kwargs),
    )
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        if failure == "missing_table":
            connection.execute("CREATE TABLE other (value TEXT)")
        else:
            connection.execute(
                "CREATE TABLE cookies (name TEXT, value TEXT, host_key TEXT, encrypted_value BLOB)"
            )
            connection.execute(
                "INSERT INTO cookies VALUES (?, ?, ?, ?)", ("auth_token", "", ".x.com", 42)
            )
        connection.commit()

        assert chrome_cookies._extract_chromium_cookies_macos(
            database, "Dummy Safe Storage", ".x.com", ["auth_token"]
        ) is None

        assert list(copies.iterdir()) == []
    finally:
        connection.close()
