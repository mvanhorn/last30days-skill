import os
import subprocess
from pathlib import Path

import pytest

from lib import grok_audit, grok_x


@pytest.mark.parametrize("version", [
    "grok 1.0.41 (5b807183dd79) [stable]",
    "grok 1.0.44 (5b807183dd79)",
    "grok 1.0.45 (0123456789ab)",
    "grok 1.0.46 (unknown) [stable]",
    "grok 1.0.46 (2765805b9442) [beta]",
    "grok 1.0.70 (f50904eaee6c) [alpha]",
    "grok 1.0.46-dev (modified)",
    "unexpected version output",
    "",
])
def test_build_below_floor_or_off_stable_never_receives_credentials(monkeypatch, version):
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
    "grok 1.0.70 (f50904eaee6c)",
    "grok 1.1.0 (0123456789ab) [stable]",
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
        return subprocess.CompletedProcess(cmd, 0, "grok 1.0.45 (0123456789ab) [stable]", "")

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


@pytest.mark.parametrize("verdict, message", [
    (grok_audit.Verdict(grok_audit.FAIL, "executed a forced shell command"),
     "failed the grok tool-boundary audit (executed a forced shell command)"),
    (grok_audit.Verdict(grok_audit.INCONCLUSIVE, "audit timed out"),
     "could not complete the grok tool-boundary audit (audit timed out); it will retry next run"),
])
def test_build_without_a_passing_audit_never_receives_credentials(monkeypatch, verdict, message):
    staged = []
    calls = []
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(grok_x, "_stage_child_home", lambda path: staged.append(path))
    monkeypatch.setattr(grok_audit, "verdict_for", lambda *args: verdict)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "grok 1.0.70 (f50904eaee6c)", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    response = grok_x._invoke("private research query", 60)
    assert message in response.get("error", "")
    assert response.get("no_retry") is True
    assert staged == []
    assert calls == [["/usr/bin/grok", "--version"]]


def test_rejected_build_is_not_retried_per_query(monkeypatch):
    calls = []
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "grok 1.0.45 (0123456789ab)", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    items, error, revoked = grok_x._run_query("OpenAI", "2026-07-14", "2026-08-13")
    assert (items, revoked) == ([], False)
    assert "unsupported grok CLI version" in error
    assert len(calls) == 1


@pytest.mark.skipif(os.name == "nt", reason="The npm launcher is a POSIX node script here")
def test_npm_launcher_runs_the_native_build_it_resolves(monkeypatch, tmp_path):
    package = tmp_path / "node_modules" / "@xai-official" / "grok"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text('{"name": "@xai-official/grok", "version": "1.0.70"}')
    launcher = package / "bin" / "grok"
    launcher.write_text("#!/usr/bin/env node\nrequire('./grok-bootstrap.js');\n")
    native = tmp_path / "grok-home" / "bin" / "grok-1.0.70"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native build")
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    monkeypatch.setattr(grok_x, "binary_path", lambda: str(launcher))
    monkeypatch.setattr(grok_x, "token_store_path", lambda: tmp_path / ".grok" / "auth.json")
    audited = []
    monkeypatch.setattr(grok_audit, "verdict_for",
                        lambda binary, line, deadline: audited.append((binary, line)) or grok_audit.Verdict(grok_audit.PASS))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd[0])
        if "--version" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "grok 1.0.70 (f50904eaee6c)", "")
        return subprocess.CompletedProcess(cmd, 0, "fixture response", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    assert grok_x._invoke("X query", 10) == {"text": "fixture response"}
    assert calls == [str(native), str(native)]
    assert audited == [(str(native), "grok 1.0.70 (f50904eaee6c)")]


@pytest.mark.parametrize("verdict", [
    grok_audit.Verdict(grok_audit.FAIL, "executed a forced shell command"),
    grok_audit.Verdict(grok_audit.INCONCLUSIVE, "audit timed out"),
])
def test_unpassed_audit_surfaces_as_an_error_not_an_empty_window(monkeypatch, verdict):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(grok_audit, "verdict_for", lambda *args: verdict)
    monkeypatch.setattr(grok_x.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(
        cmd, 0, "grok 1.0.70 (f50904eaee6c)", ""))
    result = grok_x.search_x("OpenAI", "2026-07-14", "2026-08-13", depth="quick")
    assert result["items"] == []
    assert "tool-boundary audit" in result.get("error", "")


def test_native_resolution_error_returns_a_grok_failure(monkeypatch):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def boom(*args, **kwargs):
        raise PermissionError("read-only config dir")

    monkeypatch.setattr(grok_audit, "resolve_native", boom)
    response = grok_x._invoke("X query", 10)
    assert "native grok binary" in response["error"]
    assert response["no_retry"] is True
