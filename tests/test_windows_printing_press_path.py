"""Windows Printing Press CLIs off PATH (#1434).

- The engine appends %LOCALAPPDATA%/Programs/PrintingPress/bin to its own
  PATH so Digg/arXiv/Techmeme/Yahoo Finance activate without a manual edit.
- Off-PATH prescriptions use a Windows-correct command, not bash ``export``.
- The setup summary names the "Go is required" cause instead of only telling
  the user to re-run the command that just failed.
"""

from __future__ import annotations

import os
from unittest import mock

from lib import health, setup_wizard


class _NtOs:
    """Delegates to the real os module but reports name == 'nt'.

    Patched only in health's namespace so pathlib keeps the host's Path flavour.
    """

    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


class _PosixOs(_NtOs):
    name = "posix"


def _nt():
    return mock.patch.object(health, "os", _NtOs())


def _posix():
    return mock.patch.object(health, "os", _PosixOs())


def _pp_dir(tmp_path):
    return tmp_path / "Programs" / "PrintingPress" / "bin"


class TestAddManagedBinDirToPath:
    def test_appends_existing_windows_dir(self, tmp_path):
        pp_dir = _pp_dir(tmp_path)
        pp_dir.mkdir(parents=True)
        with _nt(), mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path), "PATH": "C:\\first"}):
            added = health.add_managed_bin_dir_to_path()
            path = os.environ["PATH"]
        assert added == pp_dir
        # Appended, so anything the user already put on PATH keeps precedence.
        assert path == f"C:\\first{os.pathsep}{pp_dir}"

    def test_idempotent(self, tmp_path):
        _pp_dir(tmp_path).mkdir(parents=True)
        with _nt(), mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path), "PATH": "C:\\first"}):
            health.add_managed_bin_dir_to_path()
            assert health.add_managed_bin_dir_to_path() is None
            assert os.environ["PATH"].count(str(_pp_dir(tmp_path))) == 1

    def test_noop_when_dir_missing(self, tmp_path):
        with _nt(), mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path), "PATH": "C:\\first"}):
            assert health.add_managed_bin_dir_to_path() is None
            assert os.environ["PATH"] == "C:\\first"

    def test_noop_on_posix(self, tmp_path):
        _pp_dir(tmp_path).mkdir(parents=True)
        with _posix(), \
             mock.patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path), "PATH": "/usr/bin"}):
            assert health.add_managed_bin_dir_to_path() is None
            assert os.environ["PATH"] == "/usr/bin"


class TestPathEditPrescription:
    def test_windows_uses_powershell_not_export(self):
        with _nt():
            text = health.path_edit_prescription(r"C:\Users\u\go\bin", "digg-pp-cli")
        assert "export PATH" not in text
        assert "SetEnvironmentVariable('Path'" in text
        assert r";C:\Users\u\go\bin', 'User')" in text
        assert "restart your agent host" in text

    def test_posix_keeps_export(self):
        with _posix():
            text = health.path_edit_prescription("$HOME/.local/bin", "digg-pp-cli")
        assert text == (
            'add $HOME/.local/bin to PATH (e.g. export PATH="$HOME/.local/bin:$PATH") '
            "so digg-pp-cli resolves"
        )


_GO_STDERR = (
    "npm warn something\n"
    "Go is required to install Printing Press CLIs. Install Go with: winget install GoLang.Go\n"
)


class TestSetupSummaryNamesGo:
    def test_digg_install_failed_names_go(self):
        text = setup_wizard.get_setup_status_text(
            {"digg_action": "install_failed", "digg_stderr": _GO_STDERR}
        )
        assert "Digg CLI install failed: Go is required" in text
        assert "winget install GoLang.Go" in text
        assert setup_wizard.DIGG_INSTALL_CMD in text

    def test_pp_source_install_failed_names_go(self):
        text = setup_wizard.get_setup_status_text(
            {"pp_sources": {"arxiv": {"installed": False, "action": "install_failed", "stderr": _GO_STDERR}}}
        )
        assert "arXiv CLI install failed: Go is required" in text

    def test_other_failure_keeps_manual_rerun_line(self):
        text = setup_wizard.get_setup_status_text(
            {"digg_action": "install_failed", "digg_stderr": "network unreachable"}
        )
        assert f"Digg CLI install failed — run `{setup_wizard.DIGG_INSTALL_CMD}` manually" in text
