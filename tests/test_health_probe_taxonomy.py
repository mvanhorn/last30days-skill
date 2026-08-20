"""Tests for the uniform dependency-probe taxonomy in scripts/lib/health.py.

Covers the doctor-command probe layer (issue #692 class): every probed external
dependency (yt-dlp, Printing Press CLIs, node, ffmpeg) must report
ok | missing | broken | timeout with a package-manager-aware prescription.
The stale-shim false negative — shutil.which resolves a binary that cannot
exec — must classify as ``broken`` with a *reinstall* prescription, never as
available.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from lib import health


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    """Probes memoize per process; isolate every test."""
    health.clear_dependency_probe_cache()
    yield
    health.clear_dependency_probe_cache()


def _which_map(mapping):
    """shutil.which side_effect resolving only the names in ``mapping``."""
    def _which(name, *args, **kwargs):
        return mapping.get(name)
    return _which


def _completed(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["x"], returncode=rc, stdout=stdout, stderr=stderr)


class TestMissing:
    """Scenario 1: binary absent from PATH -> missing + installer prescription."""

    def test_ytdlp_missing_brew_prescription(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"brew": "/opt/homebrew/bin/brew"})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status == health.MISSING
        assert probe.off_path is False  # genuinely absent, not merely off-PATH
        assert probe.prescription == "brew install yt-dlp"
        assert probe.owner_pkg_manager == "brew"
        assert "not found on PATH" in probe.detail

    def test_ytdlp_missing_pipx_prescription_when_no_brew(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"pipx": "/usr/local/bin/pipx"})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status == health.MISSING
        assert probe.prescription == "pipx install yt-dlp"
        assert probe.owner_pkg_manager == "pipx"

    def test_pp_cli_missing_prescribes_printing_press_install(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("digg-pp-cli")
        assert probe.status == health.MISSING
        assert "printing-press-library" in probe.prescription
        assert "install digg --cli-only" in probe.prescription
        assert probe.owner_pkg_manager == "npx"

    def test_node_missing_nvm_prescription_when_no_brew(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NVM_DIR", str(tmp_path))
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("node")
        assert probe.status == health.MISSING
        assert "nvm install" in probe.prescription
        assert probe.owner_pkg_manager == "nvm"

    def test_ffmpeg_missing_apt_prescription_when_no_brew(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"apt-get": "/usr/bin/apt-get"})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("ffmpeg")
        assert probe.status == health.MISSING
        assert "apt-get install" in probe.prescription
        assert probe.owner_pkg_manager == "apt"

    def test_missing_with_no_manager_still_prescribes_something(self, monkeypatch):
        monkeypatch.delenv("NVM_DIR", raising=False)
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({})), \
             mock.patch.object(health, "_nvm_present", return_value=False), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status == health.MISSING
        assert probe.prescription  # never an empty prescription for missing
        assert probe.owner_pkg_manager == ""


class TestBroken:
    """Scenario 2: which resolves but exec fails -> broken + REINSTALL prescription."""

    def test_stale_shim_exec_oserror_is_broken_not_ok(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"yt-dlp": "/x/yt-dlp", "brew": "/x/brew"})), \
             mock.patch.object(health.subprocess, "run", side_effect=OSError("exec format error")):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status == health.BROKEN
        assert probe.prescription == "brew reinstall yt-dlp"
        assert "reinstall" in probe.prescription.lower()

    def test_nonzero_version_exit_is_broken(self):
        fake = _completed(rc=1, stderr="ModuleNotFoundError: No module named 'yt_dlp'")
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"yt-dlp": "/x/yt-dlp", "brew": "/x/brew"})), \
             mock.patch.object(health.subprocess, "run", return_value=fake):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status == health.BROKEN
        assert "ModuleNotFoundError" in probe.detail
        assert "reinstall" in probe.prescription.lower()

    def test_broken_pp_cli_prescribes_rerunning_install(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"digg-pp-cli": "/x/digg-pp-cli"})), \
             mock.patch.object(health.subprocess, "run", side_effect=OSError("bad exec")):
            probe = health.probe_dependency("digg-pp-cli")
        assert probe.status == health.BROKEN
        assert "printing-press-library" in probe.prescription
        assert "install digg --cli-only" in probe.prescription

    def test_real_stale_shim_on_disk(self, tmp_path, monkeypatch):
        """Integration: a real file whose shebang interpreter is gone (#692)."""
        shim = tmp_path / "fake-pp-cli"
        shim.write_text("#!/nonexistent-interpreter/python3\nprint('hi')\n")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", str(tmp_path))
        probe = health.probe_dependency("fake-pp-cli")
        assert probe.status == health.BROKEN
        fix = probe.prescription.lower()
        assert "reinstall" in fix or "re-run" in fix


class TestTimeout:
    """Scenario 3: slow probe -> timeout, distinct message, bounded budget."""

    def test_timeout_status_and_message(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"ffmpeg": "/x/ffmpeg", "brew": "/x/brew"})), \
             mock.patch.object(
                 health.subprocess, "run",
                 side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=health.PROBE_TIMEOUT),
             ):
            probe = health.probe_dependency("ffmpeg")
        assert probe.status == health.TIMEOUT
        assert "timed out" in probe.detail

    def test_probe_budget_is_bounded(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"node": "/x/node", "brew": "/x/brew"})), \
             mock.patch.object(health.subprocess, "run", return_value=_completed(stdout="v22.1.0")) as run:
            health.probe_dependency("node")
        assert run.call_args.kwargs["timeout"] <= health.PROBE_TIMEOUT


class TestOk:
    """Scenario 4: healthy binary -> ok, no prescription."""

    def test_healthy_binary_ok_no_prescription(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"yt-dlp": "/x/yt-dlp", "brew": "/x/brew"})), \
             mock.patch.object(health.subprocess, "run", return_value=_completed(stdout="2026.06.09\n")):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status == health.OK
        assert probe.ok
        assert probe.prescription == ""
        assert "2026.06.09" in probe.detail

    def test_real_healthy_binary_end_to_end(self, tmp_path, monkeypatch):
        """Integration: a real executable on a real PATH, no mocks."""
        binary = tmp_path / "fake-pp-cli"
        binary.write_text("#!/bin/sh\necho 1.2.3\n")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/bin{os.pathsep}/usr/bin")
        probe = health.probe_dependency("fake-pp-cli")
        assert probe.status == health.OK
        assert "1.2.3" in probe.detail
        assert probe.prescription == ""


class TestOffPath:
    """Scenario 5: on disk but off PATH -> missing + PATH-fix, never ok."""

    def test_off_path_binary_is_missing_with_path_fix(self, tmp_path):
        binary = tmp_path / "digg-pp-cli"
        binary.write_text("#!/bin/sh\necho ok\n")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"npx": "/x/npx"})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[tmp_path]):
            probe = health.probe_dependency("digg-pp-cli")
        assert probe.status == health.MISSING
        assert probe.off_path is True
        assert "PATH" in probe.prescription
        assert str(tmp_path) in probe.prescription or "$HOME" in probe.prescription
        assert str(binary) in probe.detail or "$HOME" in probe.detail

    def test_off_path_never_reports_ok(self, tmp_path):
        binary = tmp_path / "yt-dlp"
        binary.write_text("#!/bin/sh\necho 2026.06.09\n")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"brew": "/x/brew"})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[tmp_path]):
            probe = health.probe_dependency("yt-dlp")
        assert probe.status != health.OK
        assert not probe.ok


class TestCachingAndRegistry:
    """Probes are memoized per process; the registry covers all doctor deps."""

    def test_probe_memoized_single_subprocess(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"node": "/x/node", "brew": "/x/brew"})), \
             mock.patch.object(health.subprocess, "run", return_value=_completed(stdout="v22.1.0")) as run:
            first = health.probe_dependency("node")
            second = health.probe_dependency("node")
        assert run.call_count == 1
        assert first is second

    def test_clear_cache_reprobes(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({"node": "/x/node", "brew": "/x/brew"})), \
             mock.patch.object(health.subprocess, "run", return_value=_completed(stdout="v22.1.0")) as run:
            health.probe_dependency("node")
            health.clear_dependency_probe_cache()
            health.probe_dependency("node")
        assert run.call_count == 2

    def test_probe_dependencies_covers_known_set(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probes = health.probe_dependencies()
        assert set(probes) == set(health.KNOWN_DEPENDENCIES)
        assert {"yt-dlp", "digg-pp-cli", "node", "ffmpeg"} <= set(probes)
        for probe in probes.values():
            assert probe.status == health.MISSING
            assert probe.prescription

    def test_any_pp_cli_name_gets_printing_press_prescription(self):
        with mock.patch.object(health.shutil, "which", side_effect=_which_map({})), \
             mock.patch.object(health, "_off_path_candidate_dirs", return_value=[]):
            probe = health.probe_dependency("espn-pp-cli")
        assert probe.status == health.MISSING
        assert "install espn --cli-only" in probe.prescription
        assert probe.owner_pkg_manager == "npx"


class TestWindowsPrintingPressCandidates:
    """F15 regression (docs/solutions/integration-issues/
    digg-cli-agent-path-setup-wizard.md): the Windows managed install dir
    (%LOCALAPPDATA%/Programs/PrintingPress/bin) must be in the shared
    candidate-dir source so an installed-but-off-PATH digg-pp-cli gets a
    PATH fix, never "never installed".

    os.name is patched only in health's namespace (a delegating stub) —
    patching the global os.name would flip pathlib to WindowsPath and break
    Path construction on posix.
    """

    class _NtOs:
        """Delegates to the real os module but reports name == 'nt'."""
        name = "nt"

        def __getattr__(self, attr):
            return getattr(os, attr)

    def _nt(self):
        return mock.patch.object(health, "os", self._NtOs())

    def test_windows_dir_in_candidates_when_localappdata_set(self, tmp_path):
        with self._nt(), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
            dirs = health._off_path_candidate_dirs()
        assert tmp_path / "Programs" / "PrintingPress" / "bin" in dirs

    def test_windows_dir_absent_without_localappdata(self):
        env_clean = {k: v for k, v in os.environ.items()
                     if k.lower() != "localappdata"}
        with self._nt(), \
             mock.patch.dict(os.environ, env_clean, clear=True):
            assert health.windows_printing_press_bin_dir() is None

    def test_posix_has_no_windows_dir(self, tmp_path):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
            assert health.windows_printing_press_bin_dir() is None

    def test_windows_off_path_digg_reports_path_fix(self, tmp_path):
        # Binary present ONLY in the PrintingPress dir: missing + off_path +
        # PATH prescription — the documented failure mode said "never installed".
        pp_dir = tmp_path / "Programs" / "PrintingPress" / "bin"
        pp_dir.mkdir(parents=True)
        binary = pp_dir / "digg-pp-cli.exe"
        binary.write_text("#!/bin/sh\necho ok\n")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        fake_home = tmp_path / "home"
        with self._nt(), \
             mock.patch.dict(os.environ,
                             {"LOCALAPPDATA": str(tmp_path),
                              "GOPATH": str(tmp_path / "gopath")}), \
             mock.patch.object(health.Path, "home", return_value=fake_home), \
             mock.patch.object(health.shutil, "which",
                               side_effect=_which_map({"npx": "/x/npx"})):
            probe = health.probe_dependency("digg-pp-cli")
        assert probe.status == health.MISSING
        assert probe.off_path is True
        assert "PATH" in probe.prescription
        assert str(pp_dir) in probe.prescription or str(pp_dir) in probe.detail

    def test_setup_wizard_digg_candidates_derive_from_shared_dirs(self, tmp_path):
        # setup_wizard appends the Digg filename variants to the SAME shared
        # dir list health owns — including the .exe in the Windows managed dir.
        from lib import setup_wizard

        with self._nt(), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
            shared = health.installer_bin_dirs()
            candidates = setup_wizard._digg_bin_candidate_paths()
        pp_dir = tmp_path / "Programs" / "PrintingPress" / "bin"
        assert pp_dir in shared
        assert [c.parent for c in candidates] == shared
        assert pp_dir / "digg-pp-cli.exe" in candidates
        for candidate in candidates:
            if candidate.parent != pp_dir:
                assert candidate.name == "digg-pp-cli"
