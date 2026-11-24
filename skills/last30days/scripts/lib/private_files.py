"""Private local files."""

import errno
import os
import secrets
import stat
import tempfile
import time
from pathlib import Path

_WINDOWS_SHARING_VIOLATION = 32
_IS_JUNCTION = getattr(os.path, "isjunction", None)
_GETEUID = getattr(os, "geteuid", None)


def _group_writable_by_other_user(st: os.stat_result, euid: int) -> bool:
    """Return True if a group-writable mode grants write access to another non-root user."""
    if not (st.st_mode & stat.S_IWGRP):
        return False
    try:
        import grp
        import pwd

        pw = pwd.getpwuid(euid)
        gr = grp.getgrgid(st.st_gid)
    except (ImportError, KeyError):
        return True
    other_members = set(gr.gr_mem) - {pw.pw_name, "root"}
    if st.st_gid == 0 and not other_members:
        return False
    if st.st_gid == pw.pw_gid and gr.gr_name == pw.pw_name and not other_members:
        return False
    return True


def _verify_private_directory_chain(parent: Path) -> None:
    """Verify the parent directory and its ancestors cannot be modified by other users."""
    if _GETEUID is None:
        return
    euid = _GETEUID()
    parent_st = parent.lstat()
    if not stat.S_ISDIR(parent_st.st_mode):
        raise OSError(f"Expected a directory for private file: {parent}")
    if parent_st.st_uid not in (euid, 0):
        raise OSError(f"Refusing to use private directory owned by another user: {parent}")
    if (parent_st.st_mode & stat.S_IWOTH) or _group_writable_by_other_user(parent_st, euid):
        raise OSError(f"Refusing to use private directory writable by other users: {parent}")

    for ancestor in parent.parents:
        anc_st = ancestor.lstat()
        if not stat.S_ISDIR(anc_st.st_mode):
            raise OSError(f"Expected a directory ancestor for private file: {ancestor}")
        if anc_st.st_uid not in (euid, 0):
            raise OSError(f"Refusing to use directory under untrusted owner: {ancestor}")
        writable_by_others = bool(anc_st.st_mode & stat.S_IWOTH) or _group_writable_by_other_user(
            anc_st, euid
        )
        if writable_by_others and not (anc_st.st_mode & stat.S_ISVTX):
            raise OSError(
                f"Refusing to use directory under writable non-sticky ancestor: {ancestor}"
            )


def _reject_symlink_or_nonregular(path: Path) -> None:
    """Reject symlinks, Windows junctions, and non-regular existing paths without dereferencing."""
    if _IS_JUNCTION is not None and _IS_JUNCTION(path):
        raise OSError(f"Refusing to follow junction: {path}")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise OSError(errno.ELOOP, f"Refusing to follow symlink: {path}")
    if not stat.S_ISREG(mode):
        raise OSError(f"Expected a regular private file: {path}")


def _resolve_parent_only(path: Path) -> Path:
    """Resolve and validate only the parent directory so a leaf symlink is never dereferenced."""
    path = Path(path)
    if path.name in ("", ".", ".."):
        raise OSError(f"Expected a regular private file path: {path}")
    _reject_symlink_or_nonregular(path)
    resolved_parent = path.parent.resolve()
    _verify_private_directory_chain(resolved_parent)
    resolved = resolved_parent / path.name
    _reject_symlink_or_nonregular(resolved)
    return resolved


def replace_private_text(path: Path, content: str) -> None:
    """Publish UTF-8 text atomically from an owner-only temporary file."""
    _reject_symlink_or_nonregular(path)
    if os.name == "posix":
        _verify_private_directory_chain(path.parent.resolve())
    staged = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
    )
    try:
        with staged:
            if os.name == "posix" and hasattr(os, "fchmod"):
                os.fchmod(staged.fileno(), 0o600)
            staged.write(content)
        _reject_symlink_or_nonregular(path)
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
        if hasattr(path, "lstat"):
            _reject_symlink_or_nonregular(path)
            for suffix in ("-wal", "-shm", "-journal"):
                _reject_symlink_or_nonregular(Path(f"{path}{suffix}"))
        return path

    path = _resolve_parent_only(path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)

    if create:
        try:
            fd = os.open(
                path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow | cloexec,
                0o600,
            )
        except FileExistsError:
            pass
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise OSError(errno.ELOOP, f"Refusing to follow symlink for SQLite file: {path}") from exc
            raise
        else:
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise OSError(f"Expected a regular SQLite file: {path}")
                if stat.S_IMODE(st.st_mode) != 0o600:
                    os.fchmod(fd, 0o600)
            finally:
                os.close(fd)

    sidecars = (Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))
    for candidate in (path, *sidecars):
        try:
            fd = os.open(candidate, os.O_RDONLY | nofollow | nonblock | cloexec)
        except FileNotFoundError:
            if candidate == path:
                raise
            continue
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise OSError(errno.ELOOP, f"Refusing to follow symlink for SQLite file: {candidate}") from exc
            raise
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"Expected a regular SQLite file: {candidate}")
            if stat.S_IMODE(st.st_mode) != 0o600:
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    return path


def write_private_text(path: Path, content: str, *, encoding: str | None = None) -> int:
    """Write text atomically with owner-only permissions before any bytes on POSIX."""
    if os.name != "posix":
        _reject_symlink_or_nonregular(path)
        return path.write_text(content, encoding=encoding)

    path = _resolve_parent_only(path)
    stage_flags = (
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    stage_path: Path | None = None
    fd = -1
    for _ in range(32):
        candidate = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(candidate, stage_flags, 0o600)
            stage_path = candidate
            break
        except FileExistsError:
            continue
    if stage_path is None:
        raise FileExistsError(f"Could not allocate private staging file for {path}")

    try:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"Expected a regular private file: {stage_path}")
            os.fchmod(fd, 0o600)
            stream = os.fdopen(fd, "w", encoding=encoding)
        except BaseException:
            os.close(fd)
            raise
        with stream:
            written = stream.write(content)
            stream.flush()
        _reject_symlink_or_nonregular(path)
        os.replace(stage_path, path)
        stage_path = None
        return written
    finally:
        if stage_path is not None:
            stage_path.unlink(missing_ok=True)
