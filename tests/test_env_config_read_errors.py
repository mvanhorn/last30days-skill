"""Config file read errors: ordinary loaders keep swallowing them, opted-in callers can retry."""

import errno
import os

import pytest

from lib import env


def _write(tmp_path, text="LAST30DAYS_SEARXNG_URL=https://searx.example\n"):
    path = tmp_path / "config.env"
    path.write_text(text, encoding="utf-8")
    return path


def _fail_open(monkeypatch, path, error):
    real_open = os.open

    def flaky(target, *args, **kwargs):
        if os.fspath(target) == os.fspath(path):
            raise error
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky)


def _fail_read(monkeypatch, error):
    def flaky(fd, *args, **kwargs):
        raise error

    monkeypatch.setattr(os, "fdopen", flaky)


@pytest.mark.parametrize("site", ["open", "read"])
def test_default_loader_still_swallows_a_read_error(site, tmp_path, monkeypatch):
    path = _write(tmp_path)
    if site == "open":
        _fail_open(monkeypatch, path, PermissionError(errno.EACCES, "in use"))
    else:
        _fail_read(monkeypatch, OSError(errno.EIO, "input/output error"))

    assert env.load_env_file(path) == {}


@pytest.mark.parametrize("site", ["open", "read"])
def test_opted_in_loader_raises_a_retryable_error_without_the_path(site, tmp_path, monkeypatch):
    path = _write(tmp_path)
    cause = PermissionError(errno.EACCES, "in use") if site == "open" else OSError(errno.EIO, "io")
    if site == "open":
        _fail_open(monkeypatch, path, cause)
    else:
        _fail_read(monkeypatch, cause)

    with pytest.raises(env.ConfigReadError) as caught:
        env.load_env_file(path, raise_read_errors=True)

    assert caught.value.__cause__ is cause
    assert os.fspath(path) not in str(caught.value) and "searx" not in str(caught.value)


@pytest.mark.parametrize("error", [
    FileNotFoundError(errno.ENOENT, "missing"),
    NotADirectoryError(errno.ENOTDIR, "not a directory"),
    OSError(errno.ELOOP, "symlink refused"),
    IsADirectoryError(errno.EISDIR, "is a directory"),
])
def test_opted_in_loader_treats_absent_or_unsafe_files_as_empty(error, tmp_path, monkeypatch):
    path = _write(tmp_path)
    _fail_open(monkeypatch, path, error)

    assert env.load_env_file(path, raise_read_errors=True) == {}


def test_opted_in_loader_still_reads_a_good_file(tmp_path):
    path = _write(tmp_path)

    assert env.load_env_file(path, raise_read_errors=True) == {
        "LAST30DAYS_SEARXNG_URL": "https://searx.example",
    }


@pytest.mark.parametrize("opted_in", [False, True], ids=["default", "opted-in"])
def test_project_layer_follows_the_same_read_error_policy(opted_in, tmp_path, monkeypatch):
    project = tmp_path / "last30days.env"
    project.write_text("LAST30DAYS_SEARXNG_URL=https://project.example\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.setattr(env, "_find_project_env", lambda: project)
    _fail_open(monkeypatch, project, PermissionError(errno.EACCES, "in use"))
    policy = env.ConfigLoadPolicy(allow_project_config=True)

    if opted_in:
        with pytest.raises(env.ConfigReadError):
            env._load_config_files(policy, raise_read_errors=True)
    else:
        assert env._load_config_files(policy)[1] == {}


def test_project_layer_still_refuses_a_symlinked_file(tmp_path, monkeypatch):
    project = tmp_path / "last30days.env"
    project.write_text("LAST30DAYS_SEARXNG_URL=https://project.example\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.setattr(env, "_find_project_env", lambda: project)
    monkeypatch.setattr(env, "_is_symlink_or_junction", lambda path: path == project)
    policy = env.ConfigLoadPolicy(allow_project_config=True)

    assert env._load_config_files(policy)[1] == {}
    assert env._load_config_files(policy, raise_read_errors=True)[1] == {}
