import os
from pathlib import Path
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


@pytest.mark.parametrize(
    ("platform", "winerror", "failures", "delays", "published"),
    [
        ("nt", 32, 1, [0.05], True),
        ("nt", 32, 4, [0.05, 0.05, 0.05], False),
        ("nt", 5, 1, [], False),
        ("posix", 32, 1, [], False),
    ],
)
def test_atomic_private_text_retries_only_windows_sharing_violations(
    tmp_path, monkeypatch, platform, winerror, failures, delays, published,
):
    target = tmp_path / "cache.json"
    target.write_text("old", encoding="utf-8")
    staged_paths = []
    waits = []
    failure = PermissionError("simulated replacement failure")
    failure.winerror = winerror

    def replace(source, destination):
        stage = Path(source)
        staged_paths.append(stage)
        assert stage.read_text(encoding="utf-8") == "new"
        assert destination == target
        assert target.read_text(encoding="utf-8") == "old"
        if len(staged_paths) <= failures:
            raise failure
        os.replace(source, destination)

    monkeypatch.setattr(private_files, "os", SimpleNamespace(name=platform, replace=replace))
    monkeypatch.setattr("time.sleep", waits.append)

    if published:
        private_files.replace_private_text(target, "new")
    else:
        with pytest.raises(PermissionError) as caught:
            private_files.replace_private_text(target, "new")
        assert caught.value is failure

    assert len(staged_paths) == len(delays) + 1
    assert len(set(staged_paths)) == 1
    assert waits == delays
    assert target.read_text(encoding="utf-8") == ("new" if published else "old")
    assert list(tmp_path.iterdir()) == [target]
