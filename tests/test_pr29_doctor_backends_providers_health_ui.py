"""Regression tests for PR 29 (HIGH-055, 056, 070, 072, 074, 076).

Covers:
  - HIGH-055: Safe stderr writes & ASCII fallbacks in lib/log.py and lib/ui.py
  - HIGH-056: Resolved path execution in lib/health.py for Windows .cmd/.bat shims
  - HIGH-070: X pin diagnostics in lib/doctor.py & lib/env.py
  - HIGH-072: Doctor parity with pipeline.available_sources() & EXCLUDE_SOURCES
  - HIGH-074: Pin whitespace stripping, Grok Bot explicit pin propagation, parallel-mcp
  - HIGH-076: Gemini thinking-part skipping & OpenAI/OpenRouter extraction fixes
"""

from __future__ import annotations

import ast
import io
import json
import os
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from lib import backends, brightdata, doctor, env, grok_x, health, log, pipeline, providers, trustpilot, ui


REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib"


def _narrow_stream(encoding: str) -> tuple[io.BytesIO, io.TextIOWrapper]:
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding=encoding, errors="strict", write_through=True)
    return buf, wrapper


def _probe_dep(status_map=None, default_status=health.MISSING):
    status_map = status_map or {}

    def fake(name, timeout=health.PROBE_TIMEOUT):
        status = status_map.get(name, default_status)
        if status == health.OK:
            return health.DependencyProbe(name=name, status=health.OK, detail=f"{name} 1.0.0")
        return health.DependencyProbe(
            name=name,
            status=status,
            detail=f"{name} simulated {status}",
            prescription=f"install {name}" if status == health.MISSING else f"reinstall {name}",
            owner_pkg_manager="brew",
        )

    return fake


class _HermeticDoctor:
    def __init__(self, probe_map=None, default_status=health.MISSING):
        ytdlp_ok = (probe_map or {}).get("yt-dlp", default_status) == health.OK
        self._patches = [
            mock.patch("lib.health.probe_dependency", _probe_dep(probe_map, default_status)),
            mock.patch("lib.env.is_ytdlp_available", return_value=ytdlp_ok),
            mock.patch("lib.youtube_yt.is_ytdlp_installed", return_value=ytdlp_ok),
            mock.patch("lib.digg.cli_searches_all_topics", return_value=True),
            mock.patch("lib.bird_x.is_bird_installed", return_value=False),
            mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None),
            mock.patch(
                "lib.bird_x.get_bird_status",
                return_value={"installed": False, "authenticated": False, "username": None, "can_install": True},
            ),
            mock.patch("lib.xurl_x.is_available", side_effect=AssertionError("no live xurl")),
            mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
            mock.patch("lib.xurl_x.stored_auth_status", return_value=("missing", "no token store")),
            mock.patch("lib.backends.which", lambda name: None),
            mock.patch("lib.doctor._count_saved_briefs", return_value=0),
            mock.patch("lib.doctor._last_report_path", return_value=None),
            mock.patch("lib.doctor._probe_sources", return_value={}),
            mock.patch("lib.library_index.fts5_available", return_value=True),
            mock.patch.dict(os.environ, {}, clear=False),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        os.environ.pop("CLAUDECODE", None)
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


# ---------------------------------------------------------------------------
# HIGH-055: Safe stderr writes & ASCII fallbacks in log.py and ui.py
# ---------------------------------------------------------------------------


class TestHigh055StderrEncodingSafety:
    @pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
    def test_log_debug_and_source_log_survive_narrow_encodings(self, encoding, monkeypatch):
        buf, stream = _narrow_stream(encoding)
        monkeypatch.setenv("LAST30DAYS_DEBUG", "1")
        with redirect_stderr(stream):
            log.debug("小红书 search ✓ 🔥 — status")
            log.source_log("Xiaohongshu", "Fetched ⏳ 3 posts ✓ 你好", tty_only=False)
        text = buf.getvalue().decode(encoding)
        assert "[DEBUG]" in text
        assert "[Xiaohongshu]" in text
        assert "Fetched" in text
        assert "3 posts" in text

    @pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
    def test_buffered_non_write_through_narrow_stream_does_not_crash_on_flush(self, encoding):
        buf = io.BytesIO()
        buffered = io.TextIOWrapper(
            buf, encoding=encoding, errors="strict", line_buffering=False, write_through=False
        )
        log.safe_stderr_write("✓ 小红书 🔥\n", stream=buffered, flush=True)
        text = buf.getvalue().decode(encoding)
        assert "?" in text and text.endswith("\n")

    def test_safe_stderr_write_handles_broken_pipe_oserror_none_and_bad_codec(self):
        class _BrokenPipeStream:
            encoding = "utf-8"

            def write(self, _text):
                raise BrokenPipeError("pipe closed")

            def flush(self):
                raise OSError("flush failed")

        class _BadCodecStream:
            encoding = "nonexistent-codec-xyz"

            def __init__(self):
                self.chunks = []

            def write(self, text):
                if not text.isascii():
                    raise UnicodeEncodeError("ascii", text, 0, 1, "not ascii")
                self.chunks.append(text)

            def flush(self):
                pass

        log.safe_stderr_write("✓ hello", stream=None)
        log.safe_stderr_write("✓ hello", stream=_BrokenPipeStream())
        bad = _BadCodecStream()
        log.safe_stderr_write("✓ hello 🔥", stream=bad)
        assert "".join(bad.chunks) == "? hello ?"

        class _NonStringEncodingStream:
            encoding = 123

            def __init__(self):
                self.chunks = []

            def write(self, text):
                self.chunks.append(text)

            def flush(self):
                pass

        non_str = _NonStringEncodingStream()
        log.safe_stderr_write("✓ ok", stream=non_str)
        assert "".join(non_str.chunks) == "✓ ok"
        assert ui._stderr_supports_utf8(non_str) is True

    def test_source_log_tty_only_contract_preserved(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            log.source_log("Test", "suppressed", tty_only=True)
            log.source_log("Test", "visible", tty_only=False)
        assert buf.getvalue() == "[Test] visible\n"

    @pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
    def test_ui_spinner_and_progress_display_use_ascii_fallback_without_crash(self, encoding):
        buf, stream = _narrow_stream(encoding)
        with redirect_stderr(stream), mock.patch.object(ui, "IS_TTY", False):
            spinner = ui.Spinner("小红书 working 🔥")
            spinner.start()
            spinner.update("step 2 ✓")
            spinner.stop("done ✓")

            display = ui.ProgressDisplay("小红书 AI 🔥", show_banner=True)
            display.show_complete(source_counts={"reddit": 2, "x": 1}, display_sources=["reddit", "x"])
            display.show_cached(age_hours=1.5)
            display.show_error("failed 你好")
            display.show_web_only_complete()
            display.show_promo("both", {"available_sources": ["reddit", "grounding"]})
            display.show_promo("x")
            display.show_bird_auth_help()
            ui.print_phase("done", "phase ✓ 🔥")
        text = buf.getvalue().decode(encoding)
        assert "-> " in text
        assert "[ok] done" in text
        assert "[ok] Research complete" in text
        assert "[x] Error:" in text
        assert "Tip:" in text
        assert "Reddit [ok], X [x]" in text

    @pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
    def test_ui_tty_spinner_and_diagnostic_banner_survive_narrow_encodings(self, encoding):
        buf, stream = _narrow_stream(encoding)
        diag = {
            "available_sources": ["reddit", "xiaohongshu"],
            "has_scrapecreators": False,
            "bird_installed": True,
            "x_backend": None,
            "native_web_backend": None,
        }
        with redirect_stderr(stream), mock.patch.object(ui, "IS_TTY", True):
            spinner = ui.Spinner("working 🔥")
            spinner.running = True
            # Run a single frame of _spin without background sleep
            with mock.patch.object(ui.time, "sleep", side_effect=lambda _: setattr(spinner, "running", False)):
                spinner._spin()
            spinner.stop("finished")
            ui.show_diagnostic_banner(diag)
        with redirect_stderr(stream), mock.patch.object(ui, "IS_TTY", False):
            ui.show_diagnostic_banner(diag)
        text = buf.getvalue().decode(encoding)
        assert "|" in text
        assert "+-----------------------------------------------------+" in text
        assert "[ok] Reddit" in text
        assert "[x] X/Twitter" in text
        assert "[!] Web" in text

    def test_no_raw_sys_stderr_write_calls_in_ui_or_log(self):
        for mod_name in ("ui.py", "log.py"):
            tree = ast.parse((LIB_DIR / mod_name).read_text(encoding="utf-8"), filename=mod_name)
            raw_writes = []
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "write"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "stderr"
                    and isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "sys"
                ):
                    raw_writes.append(node.lineno)
            assert raw_writes == [], f"raw sys.stderr.write found in {mod_name}: {raw_writes}"


# ---------------------------------------------------------------------------
# HIGH-056: health.py executes resolved path for Windows .cmd/.bat shims
# ---------------------------------------------------------------------------


class TestHigh056WindowsCmdHealthProbe:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        health.clear_dependency_probe_cache()
        yield
        health.clear_dependency_probe_cache()

    @pytest.mark.parametrize(
        "name,resolved,expected_args",
        [
            ("grok", r"C:\Users\dev\AppData\Roaming\npm\grok.cmd", ["--version"]),
            ("brightdata", r"C:\Users\dev\AppData\Roaming\npm\brightdata.cmd", ["--version"]),
            ("ffmpeg", r"C:\tools\ffmpeg.bat", ["-version"]),
        ],
    )
    def test_probe_dependency_executes_resolved_cmd_shim_path(self, name, resolved, expected_args):
        seen_cmds = []

        def windows_createprocess_sim(cmd, **kwargs):
            seen_cmds.append(cmd)
            # Simulate Windows CreateProcessW without shell=True: extensionless names fail!
            if not cmd[0].lower().endswith((".cmd", ".bat", ".exe")):
                raise FileNotFoundError(f"WinError 2: cannot exec bare {cmd[0]!r}")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=f"{name} 2.0.0\n", stderr="")

        with mock.patch.object(health.shutil, "which", return_value=resolved), \
             mock.patch.object(health.subproc, "run_with_timeout", side_effect=windows_createprocess_sim):
            probe = health.probe_dependency(name)

        assert probe.status == health.OK
        assert probe.ok is True
        assert "2.0.0" in probe.detail
        assert seen_cmds == [[resolved, *expected_args]]

    def test_probe_command_executes_resolved_path(self):
        resolved = r"C:\Users\dev\AppData\Roaming\npm\grok.cmd"
        fake = subprocess.CompletedProcess(args=[resolved, "--version"], returncode=0, stdout="1.0.0", stderr="")
        with mock.patch.object(health.shutil, "which", return_value=resolved), \
             mock.patch.object(health.subprocess, "run", return_value=fake) as run_mock:
            res = health.probe_command(["grok", "--version"])
        assert res.state == health.OK
        assert run_mock.call_args.args[0] == [resolved, "--version"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable path-with-spaces test")
    def test_probe_dependency_runs_real_posix_binary_in_dir_with_spaces(self, tmp_path, monkeypatch):
        spaced_dir = tmp_path / "dir with spaces"
        spaced_dir.mkdir()
        binary = spaced_dir / "fakecli"
        binary.write_text("#!/bin/sh\necho fakecli 1.2.3\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", f"{spaced_dir}{os.pathsep}/bin{os.pathsep}/usr/bin")
        probe = health.probe_dependency("fakecli")
        assert probe.status == health.OK
        assert probe.ok is True
        assert "1.2.3" in probe.detail


# ---------------------------------------------------------------------------
# HIGH-070: X pin diagnostics in doctor.py and env.py
# ---------------------------------------------------------------------------


class TestHigh070XPinDiagnostics:
    @pytest.mark.parametrize("pin", ["xai", "xquik", "xurl", "grok", "xapi"])
    def test_unconfigured_non_bird_pin_never_overwritten_with_will_use_bird(self, pin):
        config = {
            "LAST30DAYS_X_BACKEND": pin,
            "FROM_BROWSER": "chrome",
            "BROWSER_CONSENT": "true",
            "_BROWSER_COOKIE_MODE": "plan_only",
        }
        with _HermeticDoctor(), mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            assert env.x_pending_browser_auth(config, local_only=True) is False
            assert env.x_backend_chain(config, local_only=True) == []
            record = doctor._x_record(config)

        assert record["pinned"] is True
        assert record["active_backend"] is None
        assert record["tier"] == doctor.TIER_ERROR
        assert record["status"] == health.MISSING
        assert "will use: bird" not in (record.get("note") or "")
        assert "browser cookies" not in (record.get("note") or "").lower()

    def test_pinned_bird_with_pending_browser_auth_and_xai_key_succeeds(self):
        config = {
            "LAST30DAYS_X_BACKEND": "bird",
            "FROM_BROWSER": "chrome",
            "BROWSER_CONSENT": "true",
            "_BROWSER_COOKIE_MODE": "plan_only",
            "XAI_API_KEY": "dummy-xai-key",
        }
        with _HermeticDoctor(probe_map={"node": health.OK}), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            assert env.x_pending_browser_auth(config, local_only=True) is True
            record = doctor._x_record(config)

        assert record["pinned"] is True
        assert record["status"] == health.OK
        assert record["tier"] == doctor.TIER_OK
        assert "will use: bird" in record["note"]
        assert "browser cookies" in record["note"].lower()
        assert record["fix"] == ""
        assert record["detail"] == ""

    @pytest.mark.parametrize(
        "extra_cfg",
        [
            {"LAST30DAYS_X_BACKEND": "bird", "XAI_API_KEY": "dummy-xai-key"},
            {},
        ],
    )
    def test_pending_bird_with_broken_node_is_never_marked_ready(self, extra_cfg):
        """node on PATH (is_bird_installed=True) but probe_dependency('node') fails -> BROKEN, not OK."""
        config = {
            "FROM_BROWSER": "chrome",
            "BROWSER_CONSENT": "true",
            "_BROWSER_COOKIE_MODE": "plan_only",
            **extra_cfg,
        }
        with _HermeticDoctor(probe_map={"node": health.BROKEN}), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            record = doctor._x_record(config)
            auth_path = doctor._x_auth_path(config)
            avail = pipeline.available_sources(config, local_only=True)
            report = doctor.build_report(config)

        assert record["status"] == health.BROKEN
        assert record["tier"] == doctor.TIER_ERROR
        assert record["active_backend"] is None
        assert "will use: bird" not in (record.get("note") or "")
        assert "node" in record["detail"].lower()
        assert "reinstall node" in record["fix"].lower()
        bird_entry = next(b for b in record["backends"] if b["name"] == "bird")
        assert bird_entry["status"] == health.BROKEN
        assert "x" not in avail
        assert "x" not in (report["permissions"].get("network") or {}).get("available_sources", [])
        if not extra_cfg:
            assert auth_path["armed"] is False

    def test_pinned_bird_with_broken_node_keeps_broken_error_even_with_from_browser(self):
        config = {
            "LAST30DAYS_X_BACKEND": "bird",
            "AUTH_TOKEN": "dummy-auth",
            "CT0": "dummy-ct0",
            "FROM_BROWSER": "chrome",
            "BROWSER_CONSENT": "true",
            "_BROWSER_COOKIE_MODE": "plan_only",
            "XAI_API_KEY": "dummy-xai-key",
        }
        with _HermeticDoctor(probe_map={"node": health.BROKEN}), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            record = doctor._x_record(config)

        assert record["pinned"] is True
        assert record["status"] == health.BROKEN
        assert record["tier"] == doctor.TIER_ERROR
        assert "node" in record["fix"].lower()

    def test_chained_record_blames_pinned_backend_not_broken_grok_or_bird(self):
        config = {
            "LAST30DAYS_X_BACKEND": "xquik",
            "XAI_API_KEY": "dummy-xai-key",
        }
        with _HermeticDoctor(), \
             mock.patch("lib.backends.which", lambda n: "/usr/bin/grok" if n == "grok" else None), \
             mock.patch("lib.grok_audit.cached_status", return_value=None), \
             mock.patch(
                 "lib.grok_x.stored_auth_status",
                 return_value=(grok_x.AUTH_ERROR, "corrupt grok auth.json", None),
             ):
            record = doctor._x_record(config)

        assert record["pinned"] is True
        assert record["status"] == health.MISSING
        assert record["tier"] == doctor.TIER_ERROR
        assert "XQUIK_API_KEY" in record["requires"]
        assert "XQUIK_API_KEY" in record["detail"]
        assert "XQUIK_API_KEY" in record["fix"]
        assert "grok" not in record["detail"].lower()
        assert "grok" not in record["fix"].lower()
        assert "bird" not in record["requires"].lower()

    def test_chained_record_blames_pinned_backend_when_all_backends_missing(self):
        config = {"LAST30DAYS_X_BACKEND": "xquik"}
        with _HermeticDoctor():
            record = doctor._x_record(config)

        assert record["pinned"] is True
        assert record["status"] == health.MISSING
        assert record["tier"] == doctor.TIER_ERROR
        assert "XQUIK_API_KEY" in record["requires"]
        assert "XQUIK_API_KEY" in record["detail"]
        assert "XQUIK_API_KEY" in record["fix"]


# ---------------------------------------------------------------------------
# HIGH-072: Doctor parity with pipeline.available_sources() & EXCLUDE_SOURCES
# ---------------------------------------------------------------------------


class TestHigh072DoctorPipelineParityAndExclude:
    def test_pinterest_respects_include_sources(self):
        with _HermeticDoctor():
            no_key = doctor.build_report({})["sources"]["pinterest"]
            key_only = doctor.build_report(
                {"SCRAPECREATORS_API_KEY": "dummy-sc"}
            )["sources"]["pinterest"]
            substring = doctor.build_report(
                {"SCRAPECREATORS_API_KEY": "dummy-sc", "INCLUDE_SOURCES": "notpinterest"}
            )["sources"]["pinterest"]
            opted_in = doctor.build_report(
                {"SCRAPECREATORS_API_KEY": "dummy-sc", "INCLUDE_SOURCES": "tiktok,instagram,pinterest"}
            )["sources"]["pinterest"]

        assert no_key["status"] == "unconfigured" and no_key["tier"] == doctor.TIER_OFF
        assert key_only["status"] == "opt-in" and key_only["audit_state"] == doctor.AUDIT_COULD_BE_ON
        assert substring["status"] == "opt-in"
        assert opted_in["status"] == health.OK and opted_in["tier"] == doctor.TIER_OK
        assert "pinterest" in pipeline.available_sources(
            {"SCRAPECREATORS_API_KEY": "dummy-sc", "INCLUDE_SOURCES": "tiktok,instagram,pinterest"}
        )

    def test_trustpilot_respects_include_sources_and_downgrades_when_cannot_run(self):
        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}), \
             mock.patch("lib.trustpilot.can_run_now") as can_run:
            direct_not_opted_in = doctor._trustpilot_record({})
        can_run.assert_not_called()
        assert direct_not_opted_in["status"] == "opt-in"
        assert direct_not_opted_in["tier"] == doctor.TIER_OFF

        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}), \
             mock.patch(
                 "lib.trustpilot.can_run_now",
                 return_value={"can_run": False, "session_fresh": False, "cached_companies": 0},
             ):
            not_opted_in = doctor.build_report({})["sources"]["trustpilot"]
        assert not_opted_in["status"] == "opt-in"
        assert not_opted_in["tier"] == doctor.TIER_OFF
        assert not_opted_in["audit_state"] == doctor.AUDIT_COULD_BE_ON

        # Even if a live CLI probe returns ok, un-opted-in Trustpilot stays COULD BE ON.
        doctor._apply_probe({"sources": {"trustpilot": not_opted_in}}, {"trustpilot": {"ok": True, "probed": True}})
        assert not_opted_in["audit_state"] == doctor.AUDIT_COULD_BE_ON

        # Opted in, but no fresh session and no cached companies -> DEGRADED / NOT WORKING even after probe!
        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}), \
             mock.patch(
                 "lib.trustpilot.can_run_now",
                 return_value={"can_run": False, "session_fresh": False, "cached_companies": 0},
             ):
            cannot_run = doctor.build_report({"INCLUDE_SOURCES": "trustpilot"})["sources"]["trustpilot"]
        assert cannot_run["status"] == health.DEGRADED
        assert cannot_run["tier"] == doctor.TIER_WARN
        assert cannot_run["audit_state"] == doctor.AUDIT_NOT_WORKING
        assert "auth login" in cannot_run["fix"]
        assert "cannot run now" in cannot_run["note"]
        doctor._apply_probe({"sources": {"trustpilot": cannot_run}}, {"trustpilot": {"ok": True, "probed": True}})
        assert cannot_run["audit_state"] == doctor.AUDIT_NOT_WORKING

        # Opted in + fresh session -> OK!
        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}), \
             mock.patch(
                 "lib.trustpilot.can_run_now",
                 return_value={"can_run": True, "session_fresh": True, "cached_companies": 0},
             ):
            ready = doctor.build_report({"INCLUDE_SOURCES": "trustpilot"})["sources"]["trustpilot"]
        assert ready["status"] == health.OK
        assert ready["tier"] == doctor.TIER_OK

    def test_amazon_respects_include_sources_and_probe_does_not_promote(self):
        with _HermeticDoctor(probe_map={brightdata.CLI_BIN: health.OK}), \
             mock.patch("lib.brightdata.has_credentials", return_value=True):
            not_opted = doctor.build_report({})["sources"]["amazon"]
            opted = doctor.build_report({"INCLUDE_SOURCES": "amazon"})["sources"]["amazon"]

        assert not_opted["status"] == "opt-in"
        assert not_opted["tier"] == doctor.TIER_OFF
        assert not_opted["audit_state"] == doctor.AUDIT_COULD_BE_ON
        assert "INCLUDE_SOURCES" in not_opted["fix"]
        doctor._apply_probe({"sources": {"amazon": not_opted}}, {"amazon": {"ok": True, "probed": True}})
        assert not_opted["audit_state"] == doctor.AUDIT_COULD_BE_ON

        assert opted["status"] == health.OK
        assert opted["tier"] == doctor.TIER_OK

    def test_xiaohongshu_respects_include_sources(self):
        # Direct record check proves _xiaohongshu_record makes no network calls.
        direct_not_opted = doctor._xiaohongshu_record({"XIAOHONGSHU_API_BASE": "http://localhost:18060"})
        direct_opted = doctor._xiaohongshu_record(
            {"XIAOHONGSHU_API_BASE": "http://localhost:18060", "INCLUDE_SOURCES": "xiaohongshu"}
        )
        assert direct_not_opted["status"] == "opt-in"
        assert direct_opted["status"] == health.OK

        with _HermeticDoctor(), mock.patch("lib.env.is_xiaohongshu_available", return_value=True):
            not_opted = doctor.build_report(
                {"XIAOHONGSHU_API_BASE": "http://localhost:18060"}
            )["sources"]["xiaohongshu"]
            opted = doctor.build_report(
                {"XIAOHONGSHU_API_BASE": "http://localhost:18060", "INCLUDE_SOURCES": "xiaohongshu"}
            )["sources"]["xiaohongshu"]

        assert not_opted["status"] == "opt-in"
        assert not_opted["tier"] == doctor.TIER_OFF
        assert not_opted["audit_state"] == doctor.AUDIT_COULD_BE_ON
        assert "INCLUDE_SOURCES" in not_opted["fix"]
        assert opted["status"] == health.OK
        assert opted["tier"] == doctor.TIER_OK

    def test_techmeme_parity_and_outdated_upgrade_guidance(self):
        install = health.pp_install_cmd("techmeme")
        outdated_probe = health.DependencyProbe(
            name="techmeme-pp-cli",
            status=health.OK,
            outdated=True,
            detail="2026.9.1 (older than 2026.11.1; reads only first 10 results)",
            prescription=install,
        )
        with _HermeticDoctor(), \
             mock.patch(
                 "lib.health.probe_dependency",
                 side_effect=lambda name, timeout=health.PROBE_TIMEOUT: (
                     outdated_probe if name == "techmeme-pp-cli" else _probe_dep()(name, timeout)
                 ),
             ):
            report = doctor.build_report({})
            text = doctor.render_text(report)
        rec = report["sources"]["techmeme"]
        assert rec["status"] == health.OK
        assert rec["fix"] == install
        assert "older than 2026.11.1" in rec["note"]
        assert install in text

    def test_exclude_sources_marks_excluded_and_skips_live_probes(self):
        config = {"EXCLUDE_SOURCES": "reddit, hackernews, grounding"}
        with _HermeticDoctor():
            report = doctor.build_report(config)

        for src in ("reddit", "hackernews", "web"):
            rec = report["sources"][src]
            assert rec["status"] == "excluded", src
            assert rec["tier"] == doctor.TIER_OFF, src
            assert rec["audit_state"] == doctor.AUDIT_COULD_BE_ON, src
            assert "EXCLUDE_SOURCES" in rec["note"], src
        # Non-excluded source stays active
        assert report["sources"]["polymarket"]["status"] == health.OK
        assert report["sources"]["polymarket"]["audit_state"] == doctor.AUDIT_WORKING

        # Direct _probe_source skips excluded source
        assert doctor._probe_source("hackernews", config, 5) is None

        # _probe_sources never invokes _probe_source for excluded sources
        probed_names = []

        def record_probe(name, cfg, timeout):
            probed_names.append(name)
            return {"ok": True, "detail": "ok", "probed": True}

        with mock.patch("lib.doctor._probe_source", side_effect=record_probe):
            results = doctor._probe_sources(config, timeout=5)

        assert "reddit" not in probed_names
        assert "hackernews" not in probed_names
        assert "reddit" not in results
        assert "hackernews" not in results
        assert "polymarket" in results

    def test_doctor_run_probe_omits_excluded_sources_from_probe_metadata(self):
        config = {"EXCLUDE_SOURCES": "reddit,hackernews"}
        stdout = io.StringIO()
        stderr = io.StringIO()
        with _HermeticDoctor(), \
             mock.patch("lib.doctor._write_cache", return_value=False), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            rc = doctor.run(config, emit_json=True, probe=True)
        assert rc == 0
        payload = json.loads(stdout.getvalue())
        assert "reddit" not in payload["probe"]["sources"]
        assert "hackernews" not in payload["probe"]["sources"]
        assert payload["sources"]["reddit"]["status"] == "excluded"
        assert payload["sources"]["hackernews"]["status"] == "excluded"

    @pytest.mark.parametrize("token", ["web", "grounding"])
    def test_web_and_grounding_exclusion_parity_between_doctor_and_pipeline(self, token):
        config = {"BRAVE_API_KEY": "dummy-brave", "EXCLUDE_SOURCES": token}
        assert {"web", "grounding"} <= env.exclude_sources(config)
        assert {"web", "grounding"} <= pipeline._excluded_sources(config)
        assert "grounding" not in pipeline.available_sources(config)
        with _HermeticDoctor():
            report = doctor.build_report(config)
        web_rec = report["sources"]["web"]
        assert web_rec["status"] == "excluded"
        assert web_rec["tier"] == doctor.TIER_OFF
        assert web_rec["audit_state"] == doctor.AUDIT_COULD_BE_ON
        assert f"({token})" in web_rec["note"]
        assert f"remove {token} from EXCLUDE_SOURCES" in web_rec["fix"]

    @pytest.mark.parametrize("raw_exclude", ["web,grounding", "grounding, web"])
    def test_web_and_grounding_both_excluded_lists_both_tokens_in_fix(self, raw_exclude):
        config = {"BRAVE_API_KEY": "dummy-brave", "EXCLUDE_SOURCES": raw_exclude}
        assert env.excluded_source_tokens("web", config) == ["web", "grounding"]
        with _HermeticDoctor():
            web_rec = doctor.build_report(config)["sources"]["web"]
        assert web_rec["status"] == "excluded"
        assert "web" in web_rec["note"] and "grounding" in web_rec["note"]
        assert "web" in web_rec["fix"] and "grounding" in web_rec["fix"]
        assert "remove web and grounding from EXCLUDE_SOURCES" in web_rec["fix"]

    def test_excluded_xiaohongshu_never_contacts_service_in_doctor_or_pipeline(self):
        config = {
            "XIAOHONGSHU_API_BASE": "http://localhost:18060",
            "INCLUDE_SOURCES": "xiaohongshu",
            "EXCLUDE_SOURCES": "xiaohongshu",
        }
        with mock.patch(
            "lib.env.is_xiaohongshu_available",
            side_effect=AssertionError("excluded xiaohongshu must never be probed"),
        ) as xhs_probe:
            avail_live = pipeline.available_sources(config)
            with _HermeticDoctor():
                avail_local = pipeline.available_sources(config, local_only=True)
                report = doctor.build_report(config)
        xhs_probe.assert_not_called()
        assert "xiaohongshu" not in avail_live
        assert "xiaohongshu" not in avail_local
        assert report["sources"]["xiaohongshu"]["status"] == "excluded"
        assert report["sources"]["xiaohongshu"]["tier"] == doctor.TIER_OFF

    def test_trustpilot_no_browser_reports_disabled_instead_of_login_fix(self, monkeypatch):
        config = {
            "INCLUDE_SOURCES": "trustpilot",
            trustpilot.NO_BROWSER_ENV: "1",
        }
        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}), \
             mock.patch("lib.pipeline.which", lambda name: f"/usr/bin/{name}"), \
             mock.patch("lib.trustpilot.can_run_now") as can_run:
            rec = doctor._trustpilot_record(config)
            avail = pipeline.available_sources(config, local_only=True)
        can_run.assert_not_called()
        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}), \
             mock.patch("lib.pipeline.which", lambda name: f"/usr/bin/{name}"), \
             mock.patch(
                 "lib.trustpilot.can_run_now",
                 return_value={"can_run": False, "session_fresh": False, "cached_companies": 0},
             ):
            report = doctor.build_report(config)
        assert rec["status"] == "excluded"
        assert rec["tier"] == doctor.TIER_OFF
        assert trustpilot.NO_BROWSER_ENV in rec["note"]
        assert trustpilot.NO_BROWSER_ENV in rec["fix"]
        assert "auth login" not in rec["fix"]
        assert "trustpilot" not in avail
        assert "trustpilot" not in (report["permissions"].get("network") or {}).get("available_sources", [])
        assert doctor._config_fingerprint({"INCLUDE_SOURCES": "trustpilot"}) != (
            doctor._config_fingerprint(config)
        )

        # Also honored when set only in process environment.
        monkeypatch.setenv(trustpilot.NO_BROWSER_ENV, "1")
        with _HermeticDoctor(probe_map={"trustpilot-pp-cli": health.OK}):
            env_rec = doctor._trustpilot_record({"INCLUDE_SOURCES": "trustpilot"})
        assert env_rec["status"] == "excluded"
        assert trustpilot.NO_BROWSER_ENV in env_rec["note"]
        assert "auth login" not in env_rec["fix"]


# ---------------------------------------------------------------------------
# HIGH-074: Pin whitespace stripping, Grok Bot explicit pin, parallel-mcp
# ---------------------------------------------------------------------------


class TestHigh074BackendsPinAndParallelMcp:
    def test_whitespace_padded_env_pins_match_runtime(self):
        config_xai = {"LAST30DAYS_X_BACKEND": "  xai  ", "XAI_API_KEY": "dummy-xai"}
        res_xai = backends.resolve("x", config_xai)
        assert res_xai.pinned is True
        assert res_xai.pin == "xai"
        assert res_xai.active_backend == "xai" == env.x_backend_chain(config_xai)[0]

        config_grok_bot_bird = {
            "LAST30DAYS_HOST": "grok-bot",
            "LAST30DAYS_X_BACKEND": "  bird  ",
            "AUTH_TOKEN": "dummy-auth",
            "CT0": "dummy-ct0",
        }
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True), \
             mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None), \
             mock.patch("lib.health.probe_dependency", _probe_dep({"node": health.OK})):
            res_bird = backends.resolve("x", config_grok_bot_bird)
            chain_bird = env.x_backend_chain(config_grok_bot_bird)
        assert res_bird.pinned is True
        assert res_bird.pin == "bird"
        assert res_bird.active_backend == "bird" == chain_bird[0]

        config_reddit = {
            "LAST30DAYS_REDDIT_BACKEND": "  SCRAPECREATORS  ",
            "SCRAPECREATORS_API_KEY": "dummy-sc",
        }
        res_reddit = backends.resolve("reddit", config_reddit)
        assert res_reddit.pinned is True
        assert res_reddit.pin == "scrapecreators"
        assert env.reddit_scrapecreators_primary(config_reddit) is True

    def test_explicit_pin_propagated_into_specs_for_policy_on_grok_bot(self):
        config = {
            "LAST30DAYS_HOST": "grok-bot",
            "AUTH_TOKEN": "dummy-auth",
            "CT0": "dummy-ct0",
            "XAI_API_KEY": "dummy-xai",
        }
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True), \
             mock.patch("lib.health.probe_dependency", _probe_dep({"node": health.OK})):
            res = backends.resolve("x", config, pin="  bird  ")
        assert res.pinned is True
        assert res.pin == "bird"
        assert res.active_backend == "bird"
        assert "bird" in res.chain

        with mock.patch("lib.backends.which", lambda n: "/usr/bin/grok" if n == "grok" else None), \
             mock.patch("lib.grok_audit.cached_status", return_value=None), \
             mock.patch(
                 "lib.grok_x.stored_auth_status",
                 return_value=(grok_x.AUTH_OK, "signed in", None),
             ):
            res_grok = backends.resolve("x", {"LAST30DAYS_HOST": "grok-bot"}, pin=" GROK ")
        assert res_grok.pinned is True
        assert res_grok.pin == "grok"
        assert res_grok.active_backend == "grok"

    def test_parallel_mcp_registered_opt_in_and_resolvable_when_pinned(self):
        assert "parallel-mcp" in backends.WEB_BACKEND_ORDER
        web_desc = backends.get_descriptor("web")
        spec = next(s for s in web_desc.backends if s.name == "parallel-mcp")
        assert spec.opt_in is True
        assert spec.paid is False

        # Never auto-selected when unpinned (keyless or native search).
        unpinned = backends.resolve("web", {})
        assert unpinned.active_backend == "keyless"
        native = backends.resolve("web", {"LAST30DAYS_NATIVE_SEARCH": "1"})
        assert native.active_backend is None

        # Pinned (including with surrounding whitespace / mixed case) resolves cleanly.
        pinned = backends.resolve("web", {}, pin="  Parallel-MCP ")
        assert pinned.pinned is True
        assert pinned.pin == "parallel-mcp"
        assert pinned.active_backend == "parallel-mcp"
        assert pinned.tier == backends.TIER_OK
        assert "will use: parallel-mcp" in pinned.summary
        assert "--web-backend" in pinned.summary

        # Optional PARALLEL_API_KEY is surfaced cleanly without requiring it.
        pinned_keyed = backends.resolve("web", {"PARALLEL_API_KEY": "dummy-parallel"}, pin="parallel-mcp")
        finding = next(f for f in pinned_keyed.findings if f.name == "parallel-mcp")
        assert "PARALLEL_API_KEY present" in finding.detail


# ---------------------------------------------------------------------------
# HIGH-076: Gemini thinking parts & OpenAI/OpenRouter extraction fixes
# ---------------------------------------------------------------------------


class TestHigh076ProviderTextExtraction:
    def test_extract_gemini_text_skips_thought_parts_and_joins_answer_parts(self):
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "Let me reason about {\"wrong\": true} first."},
                            {"text": '{"intent": '},
                            {"thought": False, "text": '"how_to", "count": 2}'},
                        ]
                    }
                }
            ]
        }
        extracted = providers.extract_gemini_text(payload)
        assert extracted == '{"intent": "how_to", "count": 2}'
        assert providers.extract_json(extracted) == {"intent": "how_to", "count": 2}

    def test_extract_gemini_text_thought_only_returns_empty_and_falls_to_next_candidate(self):
        thought_only = {
            "candidates": [
                {"content": {"parts": [{"thought": True, "text": "only thinking"}]}},
            ]
        }
        assert providers.extract_gemini_text(thought_only) == ""

        second_candidate = {
            "candidates": [
                {"content": {"parts": [{"thought": True, "text": "thinking"}]}},
                {"content": {"parts": [{"text": "real answer"}]}},
            ]
        }
        assert providers.extract_gemini_text(second_candidate) == "real answer"

    def test_extract_openai_text_does_not_short_circuit_on_empty_output_text(self):
        payload = {
            "output_text": "",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": '{"ok": 1}'}]}
            ],
        }
        assert providers.extract_openai_text(payload) == '{"ok": 1}'

        payload_ws = {
            "output_text": "   \n ",
            "choices": [{"message": {"content": "from choices"}}],
        }
        assert providers.extract_openai_text(payload_ws) == "from choices"

    def test_extract_openai_text_skips_reasoning_blocks_and_prefers_output_text(self):
        payload = {
            "output_text": "",
            "output": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [{"type": "summary_text", "text": "Internal reasoning"}],
                    "content": [{"type": "reasoning_text", "text": "Do not return this"}],
                },
                {"type": "thinking", "text": "Top-level thinking block"},
                {"thought": True, "text": "Top-level thought item"},
                {
                    "type": "message",
                    "content": [
                        {"type": "reasoning_text", "text": "hidden thought"},
                        {"type": "output_text", "text": '{"answer": '},
                        {"type": "output_text", "text": '"final"}'},
                    ],
                },
            ],
        }
        extracted = providers.extract_openai_text(payload)
        assert extracted == '{"answer": "final"}'
        assert providers.extract_json(extracted) == {"answer": "final"}

    def test_extract_openai_text_handles_list_typed_message_content_and_empty_item_text(self):
        payload = {
            "choices": [
                {
                    "text": "",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "text": "private thoughts"},
                            {"type": "text", "text": "hello "},
                            {"type": "text", "text": "openrouter"},
                        ],
                    },
                }
            ]
        }
        assert providers.extract_openai_text(payload) == "hello openrouter"

    def test_generate_json_works_end_to_end_on_gemini_thinking_and_openai_reasoning(self):
        gemini = providers.GeminiClient("dummy-gemini-key")
        gemini_resp = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "Thinking about schema..."},
                            {"text": '{"scores": [10, 20]}'},
                        ]
                    }
                }
            ]
        }
        with mock.patch.object(providers.http, "post", return_value=gemini_resp):
            assert gemini.generate_json(providers.GEMINI_PRO, "score") == {"scores": [10, 20]}

        openai = providers.OpenAIClient("dummy-openai-key")
        openai_resp = {
            "output_text": "",
            "output": [
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "thinking"}]},
                {"type": "message", "content": [{"type": "output_text", "text": '{"scores": [30]}'}]},
            ],
        }
        with mock.patch.object(providers.http, "post", return_value=openai_resp):
            assert openai.generate_json(providers.OPENAI_DEFAULT, "score") == {"scores": [30]}

    def test_extract_openai_text_survives_non_hashable_type_values(self):
        outer_non_hashable = {
            "output": [
                {"type": ["reasoning"], "content": [{"type": {"kind": "x"}, "text": ""}]},
                {
                    "type": {"role": "assistant"},
                    "content": [
                        {"type": ["meta"], "text": "fallback-part"},
                        {"type": "output_text", "text": '{"ok": true}'},
                    ],
                },
            ]
        }
        assert providers.extract_openai_text(outer_non_hashable) == '{"ok": true}'

        fallback_only = {
            "choices": [
                {
                    "type": ["choice"],
                    "message": {
                        "content": [
                            {"type": {"kind": "x"}, "text": "valid"},
                        ]
                    },
                }
            ]
        }
        assert providers.extract_openai_text(fallback_only) == "valid"
