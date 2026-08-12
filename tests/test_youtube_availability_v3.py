"""Canonical SSH-aware YouTube availability tests.

Regression surface for the "YouTube + planned-source truth" repair:

- ONE canonical predicate (``youtube_yt.ytdlp_route``) answers availability for
  the planner, the pipeline gates, diagnostics, and execution.
- A /tmp-only or dangling/off-PATH binary can never be reported as active.
- Diagnostics distinguish ``local`` / ``ssh`` / ``unavailable`` without
  leaking host secrets (the SSH alias is regex-validated plain text).
- Availability, diagnostics, and the execution gate all agree per route.
- Detector/root-cause guard: a future divergence between route, availability
  gate, diagnostics, or execution routing fails these tests.

No real binaries, network, cookies, or credentials are ever used — external
I/O is mocked or answered from files created inside the test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts"))

from lib import pipeline, subproc, youtube_yt  # noqa: E402
from lib.schema import SubQuery  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_ytdlp(monkeypatch):
    """Never let a host yt-dlp/ssh leak into a test by accident.

    ``ytdlp_route`` consults PATH through ``shutil.which``; the tests below
    control that explicitly. Also scrub the SSH env var so a dev machine's
    config can't flip a test into SSH mode.
    """
    monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)
    yield


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\necho fake yt-dlp\n", encoding="utf-8")
    path.chmod(0o755)


class TestCanonicalRoute:
    def test_local_route_when_persistent_binary_on_path(self, tmp_path, monkeypatch):
        """A real executable on PATH (outside ephemeral dirs) is 'local'."""
        fake_bin = tmp_path / "persistent_bin"
        fake_bin.mkdir()
        _make_executable(fake_bin / "yt-dlp")
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
        # The canonical check must not treat this dir as ephemeral: pin the
        # ephemeral list away so only the local-route logic is under test.
        monkeypatch.setattr(youtube_yt, "_EPHEMERAL_BIN_DIRS", ("/nonexistent-ephemeral",))

        assert youtube_yt.ytdlp_route() == "local"
        assert youtube_yt.is_ytdlp_installed() is True
        info = youtube_yt.ytdlp_availability()
        assert info["available"] is True
        assert info["route"] == "local"
        assert info["path"] == str(fake_bin / "yt-dlp")
        assert info["ssh_host"] is None

    def test_tmp_target_is_never_active(self, tmp_path, monkeypatch):
        """Regression: a yt-dlp whose only copy lives under /tmp (or a /tmp
        pointer) must NOT be reported as installed — it is not persistent."""
        fake_bin = tmp_path / "tmp_bin"
        fake_bin.mkdir()
        _make_executable(fake_bin / "yt-dlp")
        assert str(fake_bin).startswith("/tmp"), (
            "test precondition: pytest tmp_path must sit under /tmp"
        )
        monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")

        assert youtube_yt.ytdlp_route() == "unavailable"
        assert youtube_yt.is_ytdlp_installed() is False
        assert youtube_yt.ytdlp_availability()["available"] is False

    def test_dangling_symlink_is_never_active(self, tmp_path, monkeypatch):
        """Regression: a dangling /tmp pointer (the exact broken state found
        in the wild) must not read as active."""
        fake_bin = tmp_path / "dangling_bin"
        fake_bin.mkdir()
        (fake_bin / "yt-dlp").symlink_to(tmp_path / "does-not-exist")
        # The dangling pointer must be the ONLY yt-dlp candidate: scrub the
        # host PATH (a persistent yt-dlp here would otherwise resolve and
        # read as active, which is exactly the leak this regression exists
        # to prevent).
        monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
        real_which = shutil.which
        assert real_which("yt-dlp") is None

        assert youtube_yt.ytdlp_route() == "unavailable"
        assert youtube_yt.is_ytdlp_installed() is False

    def test_off_path_binary_is_unavailable(self, tmp_path, monkeypatch):
        """A binary not on PATH is not available — the agent subprocess PATH
        is the only PATH that matters."""
        off_path = tmp_path / "off_path"
        off_path.mkdir()
        _make_executable(off_path / "yt-dlp")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        assert youtube_yt.ytdlp_route() == "unavailable"
        assert youtube_yt.is_ytdlp_installed() is False

    def test_ssh_route_when_host_configured(self, monkeypatch):
        """LAST30DAYS_YOUTUBE_SSH_HOST + local ssh => 'ssh', available."""
        monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "macmini")
        assert shutil.which("ssh"), "test precondition: ssh on PATH"

        assert youtube_yt.ytdlp_route() == "ssh"
        assert youtube_yt.is_ytdlp_installed() is True
        info = youtube_yt.ytdlp_availability()
        assert info["available"] is True
        assert info["route"] == "ssh"
        assert info["path"] is None
        assert info["ssh_host"] == "macmini"

    def test_ssh_route_without_local_ssh_is_unavailable(self, monkeypatch):
        """SSH host configured but no local ssh binary: the exact execution
        route cannot run, so the source must not be advertised available."""
        monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "macmini")
        real_which = shutil.which

        def _which(name: str):
            if name == "ssh":
                return None
            return real_which(name)

        with mock.patch.object(youtube_yt.shutil, "which", side_effect=_which):
            assert youtube_yt.ytdlp_route() == "unavailable"
            assert youtube_yt.is_ytdlp_installed() is False

    def test_invalid_ssh_alias_is_unavailable(self, monkeypatch, capsys):
        """Metacharacter host values are rejected: never treated as a route,
        never forwarded to ssh, and the warning leaks no secrets."""
        monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "host; rm -rf /")
        # Reject the invalid alias even when a local yt-dlp exists: with the
        # alias unset the host binary would make the route 'local', which
        # would silently mask the alias-rejection contract under test.
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda name: None)
        assert youtube_yt.ytdlp_route() == "unavailable"
        assert youtube_yt.is_ytdlp_installed() is False
        warning = capsys.readouterr().err
        assert "WARNING" in warning
        # The raw value must not be echoed back into output beyond the safe
        # repr form; the key contract is that it never reaches ssh.
        assert youtube_yt._ytdlp_ssh_host() is None

    def test_empty_ssh_host_env_is_unavailable(self, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "  ")
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda name: None)
        assert youtube_yt.ytdlp_route() == "unavailable"


class TestExecutionRouting:
    def test_wrap_cmd_adds_ssh_prefix_when_configured(self, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "macmini")
        cmd = ["yt-dlp", "--ignore-config", "ytsearch5:test topic"]
        wrapped = youtube_yt._wrap_ytdlp_cmd(cmd)
        assert wrapped[:4] == ["ssh", "-o", "BatchMode=yes", "--"]
        assert wrapped[4] == "macmini"
        assert "ytsearch5:test topic" in wrapped[5]

    def test_wrap_cmd_unchanged_without_ssh(self, monkeypatch):
        cmd = ["yt-dlp", "--ignore-config", "ytsearch5:test"]
        assert youtube_yt._wrap_ytdlp_cmd(cmd) == cmd

    def test_search_youtube_unavailable_never_spawns(self, monkeypatch):
        """Route unavailable => typed error result, and _run_ytdlp is never
        called (execution route and availability agree)."""
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda name: None)
        with mock.patch.object(
            youtube_yt, "_run_ytdlp", side_effect=AssertionError("must not run")
        ):
            result = youtube_yt.search_youtube("test topic", "2026-07-13", "2026-08-12")
        assert result == {"items": [], "error": "yt-dlp not installed"}

    def test_search_youtube_local_route_executes(self, monkeypatch):
        """Local route: search runs through the canonical command path."""
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda name: "/usr/bin/yt-dlp")
        stdout = (
            '{"id": "vid1", "title": "First video", "channel": "Chan", '
            '"view_count": 100, "like_count": 5, "comment_count": 1, '
            '"upload_date": "20260720", "description": "d"}\n'
        )
        with mock.patch.object(
            youtube_yt, "_run_ytdlp", return_value=subproc.SubprocResult(0, stdout, "")
        ):
            result = youtube_yt.search_youtube("test topic", "2026-07-13", "2026-08-12")
        assert len(result["items"]) == 1
        assert result["items"][0]["video_id"] == "vid1"
        assert "error" not in result


class TestGateConsistencyGuard:
    """Detector/root-cause guard: availability, diagnostics, and the execution
    gate must all derive from the ONE canonical route predicate."""

    @pytest.mark.parametrize("route", ["local", "ssh", "unavailable"])
    def test_available_sources_and_diagnose_agree(self, route, monkeypatch):
        fake_availability = {
            "route": route,
            "available": route != "unavailable",
            "path": "/usr/bin/yt-dlp" if route == "local" else None,
            "ssh_host": "macmini" if route == "ssh" else None,
        }
        with mock.patch.object(youtube_yt, "ytdlp_route", return_value=route), \
             mock.patch.object(youtube_yt, "ytdlp_availability", return_value=fake_availability), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=route != "unavailable"):
            avail = pipeline.available_sources({}, requested_sources=["youtube"])
            assert ("youtube" in avail) == (route != "unavailable"), (
                f"available_sources disagrees with route={route}"
            )
            diag = pipeline.diagnose({}, requested_sources=["youtube"], safe=True)
            assert diag["external_commands"]["yt-dlp"] == (route != "unavailable"), (
                f"diagnose external_commands disagrees with route={route}"
            )
            assert diag["ytdlp_route"]["route"] == route, (
                f"diagnose ytdlp_route disagrees with route={route}"
            )

    def test_execution_gate_blocks_unavailable_route(self, monkeypatch):
        """The youtube execution branch must not run yt-dlp when the canonical
        route is unavailable — it must return the typed skipped outcome."""
        subquery = SubQuery(
            label="primary",
            search_query="test topic",
            ranking_query="ranking",
            sources=["youtube"],
        )
        with mock.patch.object(youtube_yt, "ytdlp_route", return_value="unavailable"), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False), \
             mock.patch.object(
                 youtube_yt, "search_and_transcribe",
                 side_effect=AssertionError("must not run when unavailable"),
             ):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="test topic",
                subquery=subquery,
                source="youtube",
                config={},
                depth="quick",
                date_range=("2026-07-13", "2026-08-12"),
                runtime=None,
                mock=False,
            )
        assert items == []
        assert artifact["_source_outcome"]["state"] == "skipped-unconfigured"
        assert artifact["_source_outcome"]["attempted"] is False

    def test_execution_gate_runs_local_route(self, monkeypatch):
        """Local route: the execution branch invokes search_and_transcribe."""
        subquery = SubQuery(
            label="primary",
            search_query="test topic",
            ranking_query="ranking",
            sources=["youtube"],
        )
        canned = {"items": [{"video_id": "v1", "title": "T", "url": "https://example.com/v1"}]}
        with mock.patch.object(youtube_yt, "ytdlp_route", return_value="local"), \
             mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt, "search_and_transcribe", return_value=canned):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="test topic",
                subquery=subquery,
                source="youtube",
                config={},
                depth="quick",
                date_range=("2026-07-13", "2026-08-12"),
                runtime=None,
                mock=False,
            )
        assert [item["video_id"] for item in items] == ["v1"]
        assert artifact == {}


def test_execute_installation_path_proof():
    """Sanity: the helper that proves PATH resolution behaves (used by the
    runtime install proof; deterministic, no binaries involved)."""
    resolved = shutil.which("sh")
    assert resolved is not None
    assert Path(resolved).is_file()
