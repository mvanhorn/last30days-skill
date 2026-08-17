"""grok as a CLI-gated dependency in the shared health layer.

The off-PATH case is confirmed real, not theoretical: on a GrokBot machine the
official installer placed the binary at ~/.grok/bin/grok and it had to be
invoked by absolute path. That is the class the Digg learning documents --
"present on disk" is not "installed".
"""

from lib import health, prescriptions


def test_grok_is_a_known_dependency():
    assert "grok" in health.KNOWN_DEPENDENCIES


def test_missing_binary_yields_an_install_prescription(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    monkeypatch.setattr(health, "_off_path_binary", lambda name: None)
    probe = health.probe_dependency("grok")
    assert probe.status == health.MISSING
    assert probe.prescription and not probe.off_path
    assert "reinstall" not in probe.prescription.lower()


def test_off_path_binary_yields_a_path_edit_not_an_install(monkeypatch, tmp_path):
    binary = tmp_path / "grok"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    monkeypatch.setattr(health, "_off_path_binary", lambda name: binary)
    probe = health.probe_dependency("grok")
    assert probe.status == health.MISSING
    assert probe.off_path is True
    assert "PATH" in probe.prescription
    assert "install" not in probe.prescription.lower().replace("installed", "")


def test_broken_binary_yields_a_reinstall_prescription(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/grok")

    def boom(*a, **k):
        raise OSError("exec format error")

    monkeypatch.setattr(health.subprocess, "run", boom)
    probe = health.probe_dependency("grok")
    assert probe.status == health.BROKEN
    assert "reinstall" in probe.prescription.lower()


def test_fallback_prescription_names_the_official_installer():
    install, reinstall = health._FALLBACK_PRESCRIPTIONS["grok"]
    assert "x.ai/cli/install.sh" in install
    assert "grok login" in install
    assert "reinstall" in reinstall.lower()


def test_registry_covers_missing_and_unauthenticated():
    """The not-logged-in state has no equivalent in the Digg pattern and is
    the one users will actually hit after installing."""
    assert ("x", "grok_cli_missing") in prescriptions.REGISTRY
    assert ("x", "grok_not_authenticated") in prescriptions.REGISTRY


def test_not_authenticated_fix_is_a_login_not_an_install():
    entry = prescriptions.REGISTRY[("x", "grok_not_authenticated")]
    assert entry.fix_cli.startswith("grok login")
    assert "no X account" in entry.fix_nl


def test_new_entries_carry_no_secret_looking_values():
    for key in (("x", "grok_cli_missing"), ("x", "grok_not_authenticated")):
        entry = prescriptions.REGISTRY[key]
        blob = f"{entry.cause} {entry.fix_nl} {entry.fix_cli}"
        assert "AUTH_TOKEN=" not in blob and "API_KEY=" not in blob
