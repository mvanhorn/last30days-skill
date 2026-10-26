"""Per-build grok tool-boundary audit (lib/grok_audit.py).

A fake grok script stands in for the binary and talks to the audit's real
local fixture over HTTP, so every check (declared tools, forced shell call,
forced file read, positive control, non-fixture traffic, timeout) runs end to
end without the CLI.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from lib import env, grok_audit

pytestmark = pytest.mark.skipif(os.name == "nt", reason="fake grok is a POSIX script")

# conftest defaults verdict_for to pass; these tests need the real one.
_REAL_VERDICT_FOR = grok_audit.verdict_for

_FAKE_GROK = r'''#!{python}
import json, os, sys, time, urllib.request

MODE = {mode!r}
argv = sys.argv[1:]
if argv == ["--version"]:
    print("grok 1.0.70 (f50904eaee6c)")
    sys.exit(0)
restricted = "--disallowed-tools" in argv
base = os.environ["GROK_XAI_API_BASE_URL"]
spec = MODE if isinstance(MODE, dict) else {{"restricted": MODE, "control": "obey"}}
behavior = spec["restricted"] if restricted else spec["control"]
if behavior == "silent":
    sys.exit(0)
if behavior == "hang":
    time.sleep(30)
if behavior == "external":
    try:
        urllib.request.urlopen("http://example.invalid/telemetry", timeout=5)
    except Exception:
        pass

def post(body):
    req = urllib.request.Request(base + "/responses", data=json.dumps(body).encode(),
                                 headers={{"content-type": "application/json"}})
    events = urllib.request.urlopen(req, timeout=10).read().decode()
    for line in events.splitlines():
        if line.startswith("data: ") and '"response.completed"' in line:
            return json.loads(line[6:])["response"]["output"]
    return []

tools = [{{"type": "x_search"}}]
if not restricted:
    tools = [{{"type": "function", "name": "run_terminal_command"}},
             {{"type": "function", "name": "read_file"}}] + tools
elif behavior == "extra_tool" or (
        behavior == "signed_in_extra"
        and os.path.isfile(os.path.join(os.environ["HOME"], ".grok", "auth.json"))):
    tools = [{{"type": "function", "name": "read_file"}}] + tools
output = post({{"model": "grok-4.7", "tools": tools, "input": []}})
if behavior == "crash":
    sys.exit(1)
results = []
execute = (not restricted and behavior == "obey") or behavior in ("execute", "leak")
for item in output:
    if item.get("type") != "function_call":
        continue
    args = json.loads(item["arguments"])
    text = "Tool not found: " + item["name"]
    if execute and item["name"] == "run_terminal_command" and behavior != "leak":
        open(args["command"].split('"')[1], "w").close()
        text = "exit: 0"
    if execute and item["name"] == "read_file":
        text = open(args["target_file"]).read()
    results.append({{"type": "function_call_output", "call_id": item["call_id"], "output": text}})
if behavior == "late_tool":
    tools = tools + [{{"type": "function", "name": "run_terminal_command"}}]
post({{"model": "grok-4.7", "tools": tools, "input": results}})
'''


def _fake_grok(tmp_path, mode, control="obey"):
    script = tmp_path / "grok"
    spec = mode if control == "obey" else {"restricted": mode, "control": control}
    script.write_text(_FAKE_GROK.format(python=sys.executable, mode=spec))
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def config_dir(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    monkeypatch.setattr(env, "CONFIG_DIR", cfg)
    monkeypatch.setattr(grok_audit, "verdict_for", _REAL_VERDICT_FOR)
    grok_audit._MEMO.clear()
    yield cfg
    grok_audit._MEMO.clear()


def _audit(binary, seconds=60):
    return grok_audit.run_audit(binary, time.monotonic() + seconds)


# --- checks -----------------------------------------------------------------

def test_build_that_refuses_forced_calls_passes(tmp_path):
    assert _audit(_fake_grok(tmp_path, "obey")) == grok_audit.Verdict(grok_audit.PASS)


@pytest.mark.parametrize("mode, check", [
    ("extra_tool", "declared tools other than hosted X search: read_file, x_search"),
    ("signed_in_extra", "declared tools other than hosted X search: read_file, x_search"),
    ("late_tool", "declared tools other than hosted X search: run_terminal_command, x_search"),
    ("execute", "executed a forced shell command"),
    ("leak", "returned a forced file read"),
])
def test_boundary_breach_fails_with_named_check(tmp_path, mode, check):
    assert _audit(_fake_grok(tmp_path, mode)) == grok_audit.Verdict(grok_audit.FAIL, check)


def test_traffic_to_another_host_fails(tmp_path):
    verdict = _audit(_fake_grok(tmp_path, "external"))
    assert verdict.status == grok_audit.FAIL
    assert "example.invalid" in verdict.check


@pytest.mark.parametrize("mode, control, check", [
    ("silent", "obey", "the CLI sent no research request to the fixture"),
    ("crash", "obey", "the CLI did not answer the forced calls"),
    ("obey", "silent", "positive control did not execute the forced calls"),
])
def test_unproven_audit_is_inconclusive(tmp_path, mode, control, check):
    verdict = _audit(_fake_grok(tmp_path, mode, control))
    assert verdict == grok_audit.Verdict(grok_audit.INCONCLUSIVE, check)


def test_audit_past_its_deadline_is_inconclusive(tmp_path):
    verdict = _audit(_fake_grok(tmp_path, "hang"), seconds=3)
    assert verdict == grok_audit.Verdict(grok_audit.INCONCLUSIVE, "audit timed out")


# --- verdict cache ----------------------------------------------------------

def _counting_audit(monkeypatch, verdict):
    calls = []

    def fake(binary, deadline):
        calls.append(binary)
        return verdict

    monkeypatch.setattr(grok_audit, "run_audit", fake)
    return calls


def test_pass_is_cached_on_disk_and_reused(monkeypatch, tmp_path, config_dir):
    binary = _fake_grok(tmp_path, "obey")
    calls = _counting_audit(monkeypatch, grok_audit.Verdict(grok_audit.PASS))
    assert grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60).passed
    grok_audit._MEMO.clear()
    assert grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60).passed
    assert len(calls) == 1
    stored = json.loads((config_dir / grok_audit.CACHE_FILENAME).read_text())
    assert [entry["verdict"] for entry in stored.values()] == ["pass"]


def test_changed_build_or_version_line_reaudits(monkeypatch, tmp_path, config_dir):
    binary = _fake_grok(tmp_path, "obey")
    calls = _counting_audit(monkeypatch, grok_audit.Verdict(grok_audit.PASS))
    grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    grok_audit.verdict_for(binary, "grok 1.0.71 (0123456789ab)", time.monotonic() + 60)
    with open(binary, "a") as fh:
        fh.write("# new build\n")
    grok_audit.verdict_for(binary, "grok 1.0.71 (0123456789ab)", time.monotonic() + 60)
    assert len(calls) == 3


def test_schema_bump_reaudits(monkeypatch, tmp_path, config_dir):
    binary = _fake_grok(tmp_path, "obey")
    calls = _counting_audit(monkeypatch, grok_audit.Verdict(grok_audit.PASS))
    grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    grok_audit._MEMO.clear()
    monkeypatch.setattr(grok_audit, "AUDIT_SCHEMA", grok_audit.AUDIT_SCHEMA + 1)
    grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    assert len(calls) == 2


def test_inconclusive_is_memoized_but_never_written(monkeypatch, tmp_path, config_dir):
    binary = _fake_grok(tmp_path, "obey")
    verdict = grok_audit.Verdict(grok_audit.INCONCLUSIVE, "audit timed out")
    calls = _counting_audit(monkeypatch, verdict)
    for _ in range(3):
        assert grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60) == verdict
    assert len(calls) == 1
    assert not (config_dir / grok_audit.CACHE_FILENAME).exists()
    grok_audit._MEMO.clear()  # next process
    grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    assert len(calls) == 2


def test_clear_failed_drops_only_fail_verdicts(monkeypatch, tmp_path, config_dir):
    good = _fake_grok(tmp_path, "obey")
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = _fake_grok(bad_dir, "execute")
    monkeypatch.setattr(grok_audit, "run_audit", lambda b, d: grok_audit.Verdict(
        grok_audit.PASS if b == good else grok_audit.FAIL, "" if b == good else "executed a forced shell command"))
    grok_audit.verdict_for(good, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    grok_audit.verdict_for(bad, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    assert grok_audit.clear_failed() == 1
    stored = json.loads((config_dir / grok_audit.CACHE_FILENAME).read_text())
    assert [entry["verdict"] for entry in stored.values()] == ["pass"]


def test_corrupt_cache_is_treated_as_absent(monkeypatch, tmp_path, config_dir):
    config_dir.mkdir()
    (config_dir / grok_audit.CACHE_FILENAME).write_text("{not json")
    binary = _fake_grok(tmp_path, "obey")
    calls = _counting_audit(monkeypatch, grok_audit.Verdict(grok_audit.PASS))
    assert grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60).passed
    assert len(calls) == 1


def test_unreadable_binary_is_inconclusive(config_dir, tmp_path):
    verdict = grok_audit.verdict_for(str(tmp_path / "missing"), "grok 1.0.70 (f50904eaee6c)",
                                     time.monotonic() + 60)
    assert verdict.status == grok_audit.INCONCLUSIVE


# --- native resolution behind the npm launcher ------------------------------

def _npm_install(tmp_path, version="1.0.70"):
    package = tmp_path / "lib" / "node_modules" / "@xai-official" / "grok"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "@xai-official/grok", "version": version}))
    launcher = package / "bin" / "grok"
    launcher.write_text("#!/usr/bin/env node\nrequire('./grok-bootstrap.js');\n")
    return package, launcher


def test_native_install_resolves_to_itself(tmp_path):
    native = tmp_path / "grok-1.0.70"
    native.write_bytes(b"\x7fELF native")
    assert grok_audit.resolve_native(str(native)) == str(native)


def test_npm_launcher_resolves_to_the_build_its_package_ships(monkeypatch, tmp_path):
    package, launcher = _npm_install(tmp_path)
    grok_home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    platform_bin = package / "node_modules" / "@xai-official" / f"grok-{grok_audit._platform_tag()}" / "bin"
    platform_bin.mkdir(parents=True)
    (platform_bin / "grok").write_bytes(b"native 1.0.70")
    assert grok_audit.resolve_native(str(launcher)) == str(platform_bin / "grok")
    # The launcher's own versioned copy wins when present, as in grok-bootstrap.js.
    (grok_home / "bin").mkdir(parents=True)
    (grok_home / "bin" / "grok-1.0.70").write_bytes(b"native 1.0.70")
    assert grok_audit.resolve_native(str(launcher)) == str(grok_home / "bin" / "grok-1.0.70")


def test_npm_launcher_without_a_native_build_is_unresolved(monkeypatch, tmp_path):
    _, launcher = _npm_install(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    assert grok_audit.resolve_native(str(launcher)) is None


def test_unrelated_script_on_path_is_not_treated_as_npm(tmp_path):
    script = tmp_path / "grok"
    script.write_text("#!/bin/sh\nexec real-grok \"$@\"\n")
    assert grok_audit.resolve_native(str(script)) == str(script)


# --- doctor surfacing -------------------------------------------------------

def _probe(monkeypatch, binary):
    from unittest import mock
    from lib import backends, grok_x

    with mock.patch("lib.backends.which", lambda name: binary if name == "grok" else None), \
         mock.patch("lib.grok_x.stored_auth_status",
                    return_value=(grok_x.AUTH_OK, "stored Grok credentials found", None)):
        return backends._probe_grok({})


def test_doctor_reports_an_unaudited_build(monkeypatch, tmp_path, config_dir):
    finding = _probe(monkeypatch, _fake_grok(tmp_path, "obey"))
    assert finding.status == "ok"
    assert "audited on its first run" in finding.detail


@pytest.mark.parametrize("verdict, status, text", [
    (grok_audit.Verdict(grok_audit.FAIL, "executed a forced shell command"), "error",
     "grok 1.0.70 (f50904eaee6c) failed the grok tool-boundary audit (executed a forced shell command)"),
    (grok_audit.Verdict(grok_audit.PASS), "ok",
     "grok 1.0.70 (f50904eaee6c) passed the tool-boundary audit"),
])
def test_doctor_reports_cached_verdict_without_a_subprocess(monkeypatch, tmp_path, config_dir,
                                                            verdict, status, text):
    import subprocess as sp

    binary = _fake_grok(tmp_path, "obey")
    monkeypatch.setattr(grok_audit, "run_audit", lambda b, d: verdict)
    grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)

    def no_subprocess(*args, **kwargs):
        raise AssertionError("doctor's grok probe must not spawn a process")

    monkeypatch.setattr(sp, "run", no_subprocess)
    finding = _probe(monkeypatch, binary)
    assert finding.status == status
    assert text in finding.detail


def test_explicit_doctor_clears_a_cached_fail(monkeypatch, tmp_path, config_dir):
    from lib import doctor

    binary = _fake_grok(tmp_path, "obey")
    monkeypatch.setattr(grok_audit, "run_audit",
                        lambda b, d: grok_audit.Verdict(grok_audit.FAIL, "executed a forced shell command"))
    grok_audit.verdict_for(binary, "grok 1.0.70 (f50904eaee6c)", time.monotonic() + 60)
    monkeypatch.setattr(doctor, "build_report", lambda config: {"run_evidence": {"fresh": True}})
    monkeypatch.setattr(doctor, "render_text", lambda report: "")
    monkeypatch.setattr(doctor, "write_cached_report", lambda *a, **k: None, raising=False)
    doctor.run({}, cached=False)
    stored = json.loads((config_dir / grok_audit.CACHE_FILENAME).read_text())
    assert stored == {}


def test_npm_launcher_decompresses_the_shipped_build_once(monkeypatch, tmp_path, config_dir):
    package, launcher = _npm_install(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    platform_bin = package / "node_modules" / "@xai-official" / f"grok-{grok_audit._platform_tag()}" / "bin"
    platform_bin.mkdir(parents=True)
    (platform_bin / "grok.br").write_bytes(b"compressed")
    calls = []

    def fake_decompress(src, dest, timeout):
        calls.append(timeout)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"native")
        return True

    monkeypatch.setattr(grok_audit, "_decompress_brotli", fake_decompress)
    dest = config_dir / "grok-bin" / "grok-1.0.70"
    assert grok_audit.resolve_native(str(launcher), decompress=False) is None
    assert grok_audit.resolve_native(str(launcher), timeout=7) == str(dest)
    assert grok_audit.resolve_native(str(launcher)) == str(dest)
    assert calls == [7]


def test_decompression_falls_back_to_grok_home_without_a_config_dir(monkeypatch, tmp_path):
    package, launcher = _npm_install(tmp_path)
    grok_home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(grok_home))
    monkeypatch.setattr(env, "CONFIG_DIR", None)
    platform_bin = package / "node_modules" / "@xai-official" / f"grok-{grok_audit._platform_tag()}" / "bin"
    platform_bin.mkdir(parents=True)
    (platform_bin / "grok.br").write_bytes(b"compressed")

    def fake_decompress(src, dest, timeout):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"native")
        return True

    monkeypatch.setattr(grok_audit, "_decompress_brotli", fake_decompress)
    assert grok_audit.resolve_native(str(launcher)) == str(grok_home / "bin" / "grok-1.0.70")


def test_unwritable_decompression_target_is_unresolved_not_raised(monkeypatch, tmp_path):
    package, launcher = _npm_install(tmp_path)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file")
    monkeypatch.setattr(env, "CONFIG_DIR", blocker)
    monkeypatch.setattr(grok_audit.shutil, "which", lambda name: "/usr/bin/node")
    platform_bin = package / "node_modules" / "@xai-official" / f"grok-{grok_audit._platform_tag()}" / "bin"
    platform_bin.mkdir(parents=True)
    (platform_bin / "grok.br").write_bytes(b"compressed")
    assert grok_audit.resolve_native(str(launcher)) is None
