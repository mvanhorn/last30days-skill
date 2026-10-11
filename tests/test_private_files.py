import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lib import private_files


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_new_file_is_private_at_creation(tmp_path):
    path = tmp_path / "state.json"
    modes_at_creation = []
    real_open = os.open

    def observe_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        modes_at_creation.append(os.fstat(fd).st_mode & 0o777)
        return fd

    old_umask = os.umask(0o022)
    try:
        with patch.object(private_files.os, "open", side_effect=observe_open):
            private_files.write_private_text(path, "private evidence")
    finally:
        os.umask(old_umask)

    assert modes_at_creation == [0o600]
    assert path.read_text() == "private evidence"


def test_windows_write_keeps_existing_file_mode(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("old content" * 20, encoding="utf-8")
    original_mode = path.stat().st_mode
    monkeypatch.setattr(private_files, "os", SimpleNamespace(name="nt"))

    private_files.write_private_text(path, "new café", encoding="utf-8")

    assert path.read_text(encoding="utf-8") == "new café"
    assert path.stat().st_mode == original_mode


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_permission_failure_closes_descriptor_without_writing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("old content", encoding="utf-8")
    descriptor = os.open(path, os.O_WRONLY)
    with patch.object(private_files.os, "open", return_value=descriptor), \
         patch.object(private_files.os, "fchmod", side_effect=PermissionError("denied")):
        with pytest.raises(PermissionError, match="denied"):
            private_files.write_private_text(path, "new content")

    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert path.read_text(encoding="utf-8") == "old content"
