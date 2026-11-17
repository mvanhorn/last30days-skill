"""Private local files."""

import os
import stat
import tempfile
import time
from pathlib import Path

_WINDOWS_SHARING_VIOLATION = 32


def replace_private_text(path: Path, content: str) -> None:
    """Publish UTF-8 text atomically from an owner-only temporary file."""
    staged = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
    )
    try:
        with staged:
            staged.write(content)
        # On Windows, a reader without delete sharing can briefly block atomic replacement.
        for retries_left in range(3, -1, -1):
            try:
                os.replace(staged.name, path)
                break
            except OSError as exc:
                if (
                    not retries_left
                    or os.name != "nt"
                    or getattr(exc, "winerror", None) != _WINDOWS_SHARING_VIOLATION
                ):
                    raise
                time.sleep(0.05)
    finally:
        Path(staged.name).unlink(missing_ok=True)


def prepare_private_sqlite_files(path: Path, *, create: bool = False) -> Path:
    """Set owner-only modes before SQLite can create files from the database mode."""
    if os.name != "posix":
        return path

    path = path.resolve()
    if create:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)

    sidecars = (Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
    for candidate in (path, *sidecars):
        try:
            mode = candidate.lstat().st_mode
            if not stat.S_ISREG(mode):
                raise OSError(f"Expected a regular SQLite file: {candidate}")
            if stat.S_IMODE(mode) != 0o600:
                candidate.chmod(0o600)
        except FileNotFoundError:
            if candidate == path:
                raise
    return path


def write_private_text(path: Path, content: str, *, encoding: str | None = None) -> int:
    """Write text with owner-only permissions before any bytes on POSIX."""
    if os.name != "posix":
        return path.write_text(content, encoding=encoding)

    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.fchmod(fd, 0o600)
        stream = os.fdopen(fd, "w", encoding=encoding)
    except Exception:
        os.close(fd)
        raise
    with stream:
        stream.truncate(0)
        return stream.write(content)
