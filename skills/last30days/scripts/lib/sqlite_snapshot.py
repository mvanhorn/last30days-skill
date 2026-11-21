"""Bounded, consistent snapshots of browser SQLite databases."""

from contextlib import closing
import logging
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

logger = logging.getLogger(__name__)

# The file-copy fallback reads a private local copy that no browser holds a
# lock on, so its only cost is copying the database; give it room for that.
_FALLBACK_MIN_TIMEOUT = 5.0
# Copies of a live database pair are retried when the browser changes either
# file mid-copy; a few tries ride out a checkpoint without spinning.
_COPY_ATTEMPTS = 3


def _file_state(*paths: Path) -> tuple:
    """Size and mtime of each file (None when absent), to detect concurrent writes."""
    state = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            state.append(None)
        else:
            state.append((stat.st_size, stat.st_mtime_ns))
    return tuple(state)


class SnapshotTimeout(sqlite3.OperationalError):
    """The browser held its database lock past the snapshot deadline."""


def _backup(reader_uri: str, destination: str, deadline: float) -> None:
    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise SnapshotTimeout("Timed out taking browser database snapshot")

    with (
        closing(sqlite3.connect(reader_uri, uri=True, timeout=0)) as reader,
        closing(sqlite3.connect(destination, timeout=0)) as writer,
    ):
        reader.backup(writer, pages=128, progress=check_deadline, sleep=0.01)


def _backup_from_local_copy(source: Path, destination: str, timeout: float) -> None:
    """Copy the database and its WAL into a private temp dir and back up from there.

    The -shm index is not copied: SQLite rebuilds it from the WAL on open, and
    a copy of a live index could disagree with the copied WAL. The temp dir
    (mode 0700 from mkdtemp) is always removed.
    """
    tmp_dir = tempfile.mkdtemp(prefix="l30d-snap-")
    try:
        local_db = Path(tmp_dir) / source.name
        wal = source.with_name(source.name + "-wal")
        local_wal = Path(tmp_dir) / wal.name
        for _attempt in range(_COPY_ATTEMPTS):
            before = _file_state(source, wal)
            shutil.copyfile(source, local_db)
            local_wal.unlink(missing_ok=True)
            if wal.is_file():
                shutil.copyfile(wal, local_wal)
            if _file_state(source, wal) == before:
                break
        else:
            # The browser checkpointed or wrote between the two copies, so the
            # pair may be torn (a database missing changes its WAL no longer
            # holds). Report a failed read rather than stale cookies.
            raise sqlite3.OperationalError("Browser database changed while copying it")
        _backup(
            local_db.as_uri(),
            destination,
            time.monotonic() + max(timeout, _FALLBACK_MIN_TIMEOUT),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def copy_snapshot(source: Path, destination: str, *, timeout: float = 1.0) -> None:
    """Back up committed database state, including WAL, to a private temp file."""
    source = Path(source)
    # Preserve the OS permission error used to explain browser-access denials;
    # sqlite3.connect otherwise turns it into a generic OperationalError.
    with source.open("rb"):
        pass

    try:
        _backup(source.resolve().as_uri() + "?mode=ro", destination, time.monotonic() + timeout)
        return
    except SnapshotTimeout:
        # A held lock is not a filesystem problem; copying the files under a
        # writer could capture a torn state, so the timeout stands.
        raise
    except sqlite3.OperationalError as exc:
        # WSL: a live WAL database on a Windows drive (/mnt/c, drvfs) fails the
        # in-place backup with "disk I/O error" because the -shm file cannot be
        # shared across the 9p boundary. Back up from a local copy instead.
        logger.debug("In-place snapshot of %s failed (%s); using a local copy", source, exc)

    _backup_from_local_copy(source, destination, timeout)
