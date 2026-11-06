"""Tests for setup-wizard detection of Printing Press CLIs -- lib/setup_wizard.py.

Setup never downloads, builds, or installs these binaries: it only detects
them and, when missing, prints the user-run pinned build command."""

from __future__ import annotations

import pytest

from lib import setup_wizard as sw


@pytest.fixture
def no_off_path(monkeypatch):
    monkeypatch.setattr(sw, "_pp_off_path_binary", lambda bin_name: None)


@pytest.fixture
def no_subprocess(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"setup must not spawn installers: {args!r}")

    monkeypatch.setattr(sw.subprocess, "run", refuse)
    monkeypatch.setattr(sw.subprocess, "Popen", refuse)


def test_already_installed_when_on_path(monkeypatch, no_subprocess):
    monkeypatch.setattr(sw.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert sw._detect_pp_cli("trustpilot-pp-cli") == (True, "already_installed", "")


def test_installed_off_path(monkeypatch, no_subprocess):
    monkeypatch.setattr(sw.shutil, "which", lambda name: None)
    monkeypatch.setattr(sw, "_pp_off_path_binary", lambda bin_name: "/home/u/.local/bin/" + bin_name)
    installed, action, off = sw._detect_pp_cli("trustpilot-pp-cli")
    assert (installed, action) == (False, "installed_off_path")
    assert off.endswith("trustpilot-pp-cli")


def test_missing_is_not_installed_and_never_runs_npx(monkeypatch, no_off_path, no_subprocess):
    # Even with npx on PATH, nothing is spawned.
    monkeypatch.setattr(sw.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    assert sw._detect_pp_cli("trustpilot-pp-cli") == (False, "not_installed", "")


def test_digg_detect_never_installs(monkeypatch, no_off_path, no_subprocess):
    monkeypatch.setattr(sw.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    monkeypatch.setattr(sw, "_digg_off_path_binary", lambda: None)
    assert sw._detect_digg_cli() == (False, "not_installed", "")


def test_default_pp_sources_empty_now_arxiv_techmeme_are_native():
    slugs = {slug for _, slug, _ in sw.PP_DEFAULT_SOURCES}
    assert "arxiv" not in slugs and "techmeme" not in slugs
    assert sw.detect_default_pp_sources() == {} or all(
        entry["action"] in {"already_installed", "installed_off_path", "not_installed"}
        for entry in sw.detect_default_pp_sources().values()
    )


def test_build_command_points_at_pinned_script():
    cmd = sw.pp_build_command("digg")
    assert cmd.startswith("bash ") and cmd.endswith(" digg")
    assert "build-pp-cli.sh" in cmd
    assert "npx" not in cmd and "printing-press-library@" not in cmd
    assert sw.DIGG_INSTALL_CMD == cmd


def test_not_installed_summary_prints_build_command_without_running_it():
    text = sw.get_setup_status_text({"digg_action": "not_installed", "pp_sources": {
        "trustpilot": {"installed": False, "action": "not_installed",
                       "build_cmd": sw.pp_build_command("trustpilot")},
    }})
    assert sw.DIGG_INSTALL_CMD in text
    assert sw.pp_build_command("trustpilot") in text
    assert "npx" not in text
