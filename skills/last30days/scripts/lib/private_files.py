"""Private local text files."""

import os
from pathlib import Path


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
