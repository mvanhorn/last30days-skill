"""Private local files."""

import os
import stat
from pathlib import Path


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
