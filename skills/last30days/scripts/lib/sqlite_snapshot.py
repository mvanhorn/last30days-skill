"""Bounded, consistent snapshots of browser SQLite databases."""

from contextlib import closing
from pathlib import Path
import sqlite3
import time


def copy_snapshot(source: Path, destination: str, *, timeout: float = 1.0) -> None:
    """Back up committed database state, including WAL, to a private temp file."""
    source = Path(source)
    # Preserve the OS permission error used to explain browser-access denials;
    # sqlite3.connect otherwise turns it into a generic OperationalError.
    with source.open("rb"):
        pass

    deadline = time.monotonic() + timeout

    def check_deadline(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError("Timed out taking browser database snapshot")

    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)) as reader,
        closing(sqlite3.connect(destination, timeout=0)) as writer,
    ):
        reader.backup(writer, pages=128, progress=check_deadline, sleep=0.01)
