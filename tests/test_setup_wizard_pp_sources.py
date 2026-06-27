"""Tests for setup-wizard auto-install of the default-on Printing Press sources
(arxiv, techmeme, trustpilot) -- lib/setup_wizard.py."""

from __future__ import annotations

import pytest

from lib import setup_wizard as sw


@pytest.fixture
def no_off_path(monkeypatch):
    # Default: no binary present on disk in known dirs.
    monkeypatch.setattr(sw, "_pp_off_path_binary", lambda bin_name: None)


def test_already_installed_when_on_path(monkeypatch):
    monkeypatch.setattr(sw.shutil, "which", lambda name: f"/usr/bin/{name}")
    installed, action, stderr, off = sw._install_pp_cli("arxiv", "arxiv-pp-cli")
    assert installed is True
    assert action == "already_installed"


def test_installed_off_path(monkeypatch, no_off_path):
    monkeypatch.setattr(sw.shutil, "which", lambda name: None)
    monkeypatch.setattr(sw, "_pp_off_path_binary", lambda bin_name: "/home/u/.local/bin/" + bin_name)
    installed, action, stderr, off = sw._install_pp_cli("techmeme", "techmeme-pp-cli")
    assert installed is False
    assert action == "installed_off_path"
    assert off.endswith("techmeme-pp-cli")


def test_no_npx(monkeypatch, no_off_path):
    monkeypatch.setattr(sw.shutil, "which", lambda name: None)  # neither bin nor npx
    installed, action, stderr, off = sw._install_pp_cli("trustpilot", "trustpilot-pp-cli")
    assert installed is False
    assert action == "no_npx"


def test_install_success(monkeypatch, no_off_path):
    # npx present; binary absent before, resolves after the install.
    calls = {"n": 0}

    def fake_which(name):
        if name == "npx":
            return "/usr/bin/npx"
        if name == "arxiv-pp-cli":
            # absent on first check, present after install
            calls["n"] += 1
            return None if calls["n"] == 1 else "/home/u/.local/bin/arxiv-pp-cli"
        return None

    monkeypatch.setattr(sw.shutil, "which", fake_which)
    monkeypatch.setattr(sw.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    installed, action, stderr, off = sw._install_pp_cli("arxiv", "arxiv-pp-cli")
    assert installed is True
    assert action == "installed"


def test_install_failed_nonzero_rc(monkeypatch, no_off_path):
    def fake_which(name):
        return "/usr/bin/npx" if name == "npx" else None

    monkeypatch.setattr(sw.shutil, "which", fake_which)
    monkeypatch.setattr(sw.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 1, "stdout": "", "stderr": "boom"})())
    installed, action, stderr, off = sw._install_pp_cli("techmeme", "techmeme-pp-cli")
    assert installed is False
    assert action == "install_failed"
    assert "boom" in stderr


def test_install_default_pp_sources_covers_default_on_pair(monkeypatch):
    # Only the zero-auth default-on sources are auto-installed. Trustpilot is
    # opt-in (INCLUDE_SOURCES=trustpilot) and intentionally excluded here.
    monkeypatch.setattr(sw.shutil, "which", lambda name: f"/usr/bin/{name}")
    out = sw.install_default_pp_sources()
    assert set(out.keys()) == {"arxiv", "techmeme"}
    assert "trustpilot" not in out
    for entry in out.values():
        assert entry["action"] == "already_installed"
        assert entry["installed"] is True


def test_install_is_idempotent(monkeypatch):
    monkeypatch.setattr(sw.shutil, "which", lambda name: f"/usr/bin/{name}")
    run_calls = []
    monkeypatch.setattr(sw.subprocess, "run", lambda *a, **k: run_calls.append(a))
    sw.install_default_pp_sources()
    sw.install_default_pp_sources()
    # All already on PATH -> npx install never invoked.
    assert run_calls == []
