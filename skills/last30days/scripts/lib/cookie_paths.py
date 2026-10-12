"""Path probes that preserve browser-data permission failures."""

import stat
from pathlib import Path


def _mode(path: Path) -> int | None:
    try:
        return path.stat().st_mode
    except PermissionError:
        raise
    except OSError:
        return None


def path_exists(path: Path) -> bool:
    return _mode(path) is not None


def path_is_file(path: Path) -> bool:
    mode = _mode(path)
    return mode is not None and stat.S_ISREG(mode)


def path_is_dir(path: Path) -> bool:
    mode = _mode(path)
    return mode is not None and stat.S_ISDIR(mode)
