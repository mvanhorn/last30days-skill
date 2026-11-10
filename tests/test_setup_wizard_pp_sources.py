"""Tests for setup-wizard auto-install of the default-on Printing Press sources
(arxiv, techmeme, yahoo_finance; trustpilot stays opt-in) -- lib/setup_wizard.py."""

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


def test_install_uses_resolved_windows_npx_path(monkeypatch, no_off_path):
    """Regression for #904: subprocess.run must receive the resolved npx
    path (e.g. a Windows PATHEXT-resolved npx.CMD), not the bare "npx"
    string -- bare "npx" fails with WinError 2 on Windows."""
    calls = {"n": 0}
    windows_npx = r"C:\Program Files\nodejs\npx.CMD"

    def fake_which(name):
        if name == "npx":
            return windows_npx
        if name == "techmeme-pp-cli":
            calls["n"] += 1
            return None if calls["n"] == 1 else r"C:\Users\me\.local\bin\techmeme-pp-cli"
        return None

    run_calls = []

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(sw.shutil, "which", fake_which)
    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    installed, action, stderr, off = sw._install_pp_cli("techmeme", "techmeme-pp-cli")
    assert installed is True
    assert action == "installed"
    assert run_calls[0][0] == windows_npx


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


def test_install_default_pp_sources_covers_default_on_set(monkeypatch):
    # Only the zero-auth default-on sources are auto-installed. Trustpilot is
    # opt-in (INCLUDE_SOURCES=trustpilot) and intentionally excluded here.
    monkeypatch.setattr(sw.shutil, "which", lambda name: f"/usr/bin/{name}")
    out = sw.install_default_pp_sources()
    assert set(out.keys()) == {"arxiv", "techmeme", "yahoo_finance"}
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


# --- Yahoo Finance: source key and catalog slug differ (underscore vs hyphen) ---

YF_BIN = "yahoo-finance-pp-cli"
YF_OFF_PATH = "/home/u/.local/bin/" + YF_BIN


def _yahoo_outcome(monkeypatch, *, before, after, npx=True, rc=0, on_disk_before=False, on_disk_after=False):
    """Run the default install with every other CLI already on PATH and the
    Yahoo Finance binary in the given state before and after the npx run.

    Returns ``(yahoo entry, argv of each npx run)``. No real install runs:
    ``shutil.which``, the off-PATH probe and ``subprocess.run`` are stubbed.
    """
    state = {"ran": False}
    runs = []

    def fake_which(name):
        if name == YF_BIN:
            present = after if state["ran"] else before
            return f"/usr/bin/{name}" if present else None
        if name == "npx":
            return "/usr/bin/npx" if npx else None
        return f"/usr/bin/{name}"

    def fake_off_path(bin_name):
        if bin_name != YF_BIN:
            return None
        return YF_OFF_PATH if (on_disk_after if state["ran"] else on_disk_before) else None

    def fake_run(cmd, **kwargs):
        runs.append(cmd)
        state["ran"] = True
        return type("P", (), {"returncode": rc, "stdout": "", "stderr": "boom" if rc else ""})()

    monkeypatch.setattr(sw.shutil, "which", fake_which)
    monkeypatch.setattr(sw, "_pp_off_path_binary", fake_off_path)
    monkeypatch.setattr(sw.subprocess, "run", fake_run)
    return sw.install_default_pp_sources()["yahoo_finance"], runs


def test_yahoo_finance_installed(monkeypatch):
    entry, runs = _yahoo_outcome(monkeypatch, before=False, after=True)
    assert entry == {"installed": True, "action": "installed"}
    # The catalog install takes the hyphenated slug, not the source key.
    assert runs == [["/usr/bin/npx", "-y", sw.PRINTING_PRESS_NPM, "install", "yahoo-finance", "--cli-only"]]


def test_yahoo_finance_already_installed(monkeypatch):
    entry, runs = _yahoo_outcome(monkeypatch, before=True, after=True)
    assert entry == {"installed": True, "action": "already_installed"}
    assert runs == []


def test_yahoo_finance_already_on_disk_off_path(monkeypatch):
    # A binary on disk that the agent PATH cannot see is never reinstalled and
    # never counted as active.
    entry, runs = _yahoo_outcome(monkeypatch, before=False, after=False, on_disk_before=True)
    assert entry == {"installed": False, "action": "installed_off_path", "path": YF_OFF_PATH}
    assert runs == []


def test_yahoo_finance_install_lands_off_path(monkeypatch):
    entry, runs = _yahoo_outcome(monkeypatch, before=False, after=False, on_disk_after=True)
    assert entry == {"installed": False, "action": "installed_off_path", "path": YF_OFF_PATH}
    assert len(runs) == 1


def test_yahoo_finance_install_failed(monkeypatch):
    entry, runs = _yahoo_outcome(monkeypatch, before=False, after=False, rc=1)
    assert entry == {"installed": False, "action": "install_failed", "stderr": "boom"}


def test_yahoo_finance_no_npx(monkeypatch):
    entry, runs = _yahoo_outcome(monkeypatch, before=False, after=False, npx=False)
    assert entry == {"installed": False, "action": "no_npx"}
    assert runs == []


def _status_text(entry):
    return sw.get_setup_status_text({"pp_sources": {"yahoo_finance": entry}})


@pytest.mark.parametrize("action", ["install_failed", "no_npx"])
def test_manual_install_hint_prints_the_catalog_slug(action):
    text = _status_text({"installed": False, "action": action})
    assert f"npx -y {sw.PRINTING_PRESS_NPM} install yahoo-finance --cli-only" in text
    assert "install yahoo_finance" not in text
    assert "Yahoo Finance CLI" in text


def test_status_text_names_yahoo_finance_active_only_on_path():
    assert "Installed Yahoo Finance CLI (Yahoo Finance source now active)" in _status_text(
        {"installed": True, "action": "installed"}
    )
    off_path = _status_text({"installed": False, "action": "installed_off_path", "path": YF_OFF_PATH})
    assert f"Yahoo Finance CLI at {YF_OFF_PATH} but not on PATH" in off_path
    assert "now active" not in off_path


def test_every_default_source_has_a_display_name_and_slug():
    # A source missing from either map falls back to the source key, which is
    # the wrong install argument for a hyphenated slug.
    for source_key, slug, _bin_name in sw.PP_DEFAULT_SOURCES:
        text = sw.get_setup_status_text(
            {"pp_sources": {source_key: {"installed": False, "action": "install_failed"}}}
        )
        assert f"install {slug} --cli-only" in text, source_key
