"""Bounded, consistent snapshots of browser SQLite databases."""

from contextlib import closing, nullcontext
import hashlib
from pathlib import Path
import sqlite3
import struct
import tempfile
import time


_WAL_SHM_ERRORS = {
    sqlite3.SQLITE_IOERR_SHMOPEN,
    sqlite3.SQLITE_IOERR_SHMSIZE,
    sqlite3.SQLITE_IOERR_SHMLOCK,
    sqlite3.SQLITE_IOERR_SHMMAP,
}
_WAL_HEADER_SIZE = 32
_WAL_FRAME_HEADER_SIZE = 24


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise sqlite3.OperationalError("Timed out taking browser database snapshot")


def _backup(source_uri: str, destination: str, deadline: float) -> None:
    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        _check_deadline(deadline)

    _check_deadline(deadline)
    with (
        closing(sqlite3.connect(source_uri, uri=True, timeout=0)) as reader,
        closing(sqlite3.connect(destination, timeout=0)) as writer,
    ):
        reader.backup(writer, pages=128, progress=check_deadline, sleep=0.01)


def _file_state(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _fingerprint(source: Path, deadline: float, *, copy_to: Path | None = None) -> bytes:
    digest = hashlib.sha256()
    with source.open("rb") as reader, (copy_to.open("xb") if copy_to else nullcontext()) as target:
        while True:
            _check_deadline(deadline)
            block = reader.read(128 * 1024)
            if not block:
                return digest.digest()
            digest.update(block)
            if target is not None:
                target.write(block)


def _validate_wal(wal: Path) -> None:
    with wal.open("rb") as reader:
        header = reader.read(_WAL_HEADER_SIZE)
    if len(header) != _WAL_HEADER_SIZE:
        raise sqlite3.OperationalError("Windows Firefox WAL has an incomplete header")
    magic, version, page_size = struct.unpack_from(">III", header)
    if (
        magic not in (0x377F0682, 0x377F0683)
        or version != 3_007_000
        or not 512 <= page_size <= 65536
        or page_size & (page_size - 1)
    ):
        raise sqlite3.OperationalError("Windows Firefox WAL has an invalid header")
    # SQLite silently ignores a torn first frame. Leave recovery of any
    # committed prefix ahead of a torn trailing frame to SQLite.
    if wal.stat().st_size < _WAL_HEADER_SIZE + _WAL_FRAME_HEADER_SIZE + page_size:
        raise sqlite3.OperationalError("Windows Firefox WAL has no complete frame")


def _backup_wsl_wal(source: Path, destination: str, deadline: float) -> None:
    wal = source.with_name(source.name + "-wal")
    state = _file_state(source), _file_state(wal)
    if not state[1][2]:
        raise sqlite3.OperationalError("Windows Firefox WAL is empty")

    with tempfile.TemporaryDirectory(prefix="last30days-firefox-") as scratch:
        local_db = Path(scratch) / source.name
        local_wal = local_db.with_name(wal.name)
        db_hash = _fingerprint(source, deadline, copy_to=local_db)
        wal_hash = _fingerprint(wal, deadline, copy_to=local_wal)

        # A checkpoint or WAL reset can race the copy. Re-read both files
        # and reject changes before letting SQLite replay the private pair
        # using its own local shared-memory index.
        if (
            (db_hash, wal_hash) != (_fingerprint(source, deadline), _fingerprint(wal, deadline))
            or state != (_file_state(source), _file_state(wal))
        ):
            raise sqlite3.OperationalError("Windows Firefox database changed during snapshot")
        _validate_wal(local_wal)
        _backup(local_db.as_uri() + "?mode=ro", destination, deadline)


def copy_snapshot(
    source: Path, destination: str, *, timeout: float = 1.0, wsl_wal_fallback: bool = False,
) -> None:
    """Back up committed database state, including WAL, to a private temp file."""
    source = Path(source)
    # Preserve the OS permission error used to explain browser-access denials;
    # sqlite3.connect otherwise turns it into a generic OperationalError.
    with source.open("rb"):
        pass

    deadline = time.monotonic() + timeout
    source = source.resolve()
    try:
        _backup(source.as_uri() + "?mode=ro", destination, deadline)
    except sqlite3.OperationalError as exc:
        if not wsl_wal_fallback or getattr(exc, "sqlite_errorcode", None) not in _WAL_SHM_ERRORS:
            raise
        _backup_wsl_wal(source, destination, deadline)
