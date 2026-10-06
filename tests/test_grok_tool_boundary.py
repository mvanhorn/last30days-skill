import os
import subprocess
from pathlib import Path

import pytest

from lib import grok_x


@pytest.mark.parametrize("version", [
    "grok 1.0.41 (old) [stable]",
    "grok 1.0.47 (future) [stable]",
    "grok 2.0.0 (future) [stable]",
    "grok 1.0.46 (unknown) [stable]",
    "grok 1.0.46 (2765805b9442) [beta]",
    "grok 1.0.46-dev (modified)",
    "unexpected version output",
    "",
])
def test_unaudited_cli_never_receives_credentials_or_research(monkeypatch, version):
    calls = []
    staged = []
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(grok_x, "_stage_child_home", lambda path: staged.append(path))

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, version, "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    response = grok_x._invoke("private research query", 60)
    assert "unsupported grok CLI version" in response.get("error", "")
    assert staged == []
    assert [cmd for cmd, _ in calls] == [["/usr/bin/grok", "--version"]]
    assert calls[0][1]["timeout"] <= 5
    assert "private research query" not in str(calls)


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "oserror"])
def test_failed_version_check_never_stages_credentials(monkeypatch, failure):
    staged = []
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(grok_x, "_stage_child_home", lambda path: staged.append(path))

    def fake_run(cmd, **kwargs):
        assert cmd == ["/usr/bin/grok", "--version"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 5)
        if failure == "oserror":
            raise OSError("cannot launch fixture")
        return subprocess.CompletedProcess(cmd, 1, "grok 1.0.46 (2765805b9442) [stable]", "failure")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    response = grok_x._invoke("private research query", 60)
    assert "version" in response.get("error", "")
    assert staged == []


@pytest.mark.parametrize("version", [
    "grok 1.0.46 (2765805b9442)",
    "grok 1.0.46 (2765805b9442) [stable]",
])
def test_audited_build_runs_x_with_only_staged_auth(monkeypatch, tmp_path, version):
    store = tmp_path / ".grok" / "auth.json"
    store.parent.mkdir()
    store.write_text('{"key": "dummy-offline-key"}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setenv("XAI_API_KEY", "dummy-unrelated-key")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        child_home = Path(kwargs["env"]["HOME"])
        assert "XAI_API_KEY" not in kwargs["env"]
        if cmd == ["/usr/bin/grok", "--version"]:
            assert not (child_home / ".grok" / "auth.json").exists()
            return subprocess.CompletedProcess(cmd, 0, version + "\n", "")
        assert (child_home / ".grok" / "auth.json").read_text() == store.read_text()
        assert cmd[1:3] == ["-p", "Use x_keyword_search for OpenAI"]
        assert "--tools" not in cmd
        assert "--disable-web-search" in cmd
        assert "x_search" not in cmd[cmd.index("--disallowed-tools") + 1].split(",")
        return subprocess.CompletedProcess(cmd, 0, '{"text":"fixture X response"}', "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    assert grok_x._invoke("Use x_keyword_search for OpenAI", 60) == {
        "text": '{"text":"fixture X response"}',
    }
    assert len(calls) == 2


@pytest.mark.parametrize("spent", [4, 10])
def test_preflight_time_counts_toward_research_timeout(monkeypatch, tmp_path, spent):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(grok_x.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(grok_x, "token_store_path", lambda: tmp_path / ".grok" / "auth.json")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--version" in cmd:
            assert kwargs["timeout"] == 5
            clock[0] += spent
            return subprocess.CompletedProcess(cmd, 0, "grok 1.0.46 (2765805b9442) [stable]", "")
        assert kwargs["timeout"] == 10 - spent
        return subprocess.CompletedProcess(cmd, 0, "fixture response", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    response = grok_x._invoke("X query", 10)
    if spent < 10:
        assert response == {"text": "fixture response"}
        assert len(calls) == 2
    else:
        assert "timed out" in response.get("error", "")
        assert len(calls) == 1


def test_search_reports_unsupported_build_as_source_failure(monkeypatch):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        assert cmd == ["/usr/bin/grok", "--version"]
        return subprocess.CompletedProcess(cmd, 0, "grok 1.0.47 (future) [stable]", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    result = grok_x.search_x("OpenAI", "2026-07-14", "2026-08-13", depth="quick")
    assert result["items"] == []
    assert "unsupported grok CLI version" in result.get("error", "")


def test_search_reports_profile_write_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(grok_x, "token_store_path", lambda: tmp_path / "missing" / "auth.json")

    def fake_run(cmd, **kwargs):
        assert cmd == ["/usr/bin/grok", "--version"]
        return subprocess.CompletedProcess(cmd, 0, "grok 1.0.46 (2765805b9442) [stable]", "")

    def denied(*args, **kwargs):
        raise PermissionError("cannot write agent profile")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(Path, "write_text", denied)
    result = grok_x.search_x("OpenAI", "2026-07-14", "2026-08-13", depth="quick")
    assert result["items"] == []
    assert "cannot write agent profile" in result.get("error", "")


@pytest.mark.skipif(os.name == "nt", reason="The POSIX Grok installer uses a versioned symlink")
def test_updater_symlink_cannot_change_build_after_version_check(monkeypatch, tmp_path):
    audited = tmp_path / "grok-1.0.46"
    future = tmp_path / "grok-future"
    audited.touch()
    future.touch()
    launcher = tmp_path / "grok"
    launcher.symlink_to(audited)
    monkeypatch.setattr(grok_x, "binary_path", lambda: str(launcher))
    monkeypatch.setattr(grok_x, "token_store_path", lambda: tmp_path / ".grok" / "auth.json")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--version" in cmd:
            launcher.unlink()
            launcher.symlink_to(future)
            return subprocess.CompletedProcess(cmd, 0, "grok 1.0.46 (2765805b9442)", "")
        assert cmd[0] == str(audited)
        return subprocess.CompletedProcess(cmd, 0, "fixture response", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    assert grok_x._invoke("X query", 10) == {"text": "fixture response"}
    assert calls[0] == [str(audited), "--version"]
