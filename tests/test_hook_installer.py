"""Tests for cross-harness brief-capture hook installation, removal, and doctor status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import doctor, hook_installer


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_SCRIPT = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"


def _run_cli(
    *args: str,
    home: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LAST30DAYS_MEMORY_DIR"] = str(home / "Documents" / "Last30Days")
    env.pop("LAST30DAYS_HOST", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("harness", hook_installer.SUPPORTED_HARNESSES)
def test_install_idempotent_and_uninstall_preserves_existing_hooks(tmp_path, harness):
    script = tmp_path / "capture_brief_hook.py"
    script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    # Seed pre-existing user configuration with an unrelated hook.
    if harness == "claude":
        cfg = tmp_path / ".claude" / "settings.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "Stop": [
                            {"hooks": [{"type": "command", "command": "echo keep-me"}]}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )
    elif harness == "antigravity":
        cfg = tmp_path / ".agents" / "hooks.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps(
                {
                    "user-lint": {
                        "Stop": [{"type": "command", "command": "echo keep-me"}]
                    }
                }
            ),
            encoding="utf-8",
        )
    elif harness == "gemini":
        cfg = tmp_path / ".gemini" / "settings.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps(
                {
                    "hooks": {
                        "AfterAgent": [
                            {
                                "matcher": "*",
                                "hooks": [
                                    {
                                        "name": "user-hook",
                                        "type": "command",
                                        "command": "echo keep-me",
                                        "timeout": 3000,
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
    elif harness == "codex":
        cfg = tmp_path / ".codex" / "hooks.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {"hooks": [{"type": "command", "command": "echo keep-me"}]}
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
    elif harness == "cursor":
        cfg = tmp_path / ".cursor" / "hooks.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "afterAgentResponse": [{"command": "echo keep-me"}]
                    },
                }
            ),
            encoding="utf-8",
        )
    elif harness == "grok":
        cfg = tmp_path / ".grok" / "hooks" / "user-other.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")

    first = hook_installer.install_hooks(harness, home=tmp_path, script_path=script)
    assert first["ok"] is True
    assert first["results"][harness]["status"] == "installed"
    if harness == "codex":
        assert "/hooks" in first["results"][harness].get("note", "")

    # Second install must be idempotent (no duplicate entries).
    second = hook_installer.install_hooks(harness, home=tmp_path, script_path=script)
    assert second["ok"] is True
    assert second["results"][harness]["status"] == "already-installed"

    status = hook_installer.hook_status(home=tmp_path)
    assert status[harness]["status"] == "installed"

    # Verify schema details & preservation of user entries.
    if harness == "gemini":
        data = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
        entries = data["hooks"]["AfterAgent"]
        assert len(entries) == 2
        assert entries[1]["hooks"][0]["timeout"] == 5000
    elif harness == "antigravity":
        data = json.loads((tmp_path / ".agents" / "hooks.json").read_text(encoding="utf-8"))
        assert "user-lint" in data
        assert "last30days-brief-capture" in data
        assert "Stop" in data["last30days-brief-capture"]
    elif harness == "cursor":
        data = json.loads((tmp_path / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["hooks"]["afterAgentResponse"]) == 2
    elif harness == "grok":
        assert (tmp_path / ".grok" / "hooks" / "last30days-brief-capture.json").is_file()
        assert (tmp_path / ".grok" / "hooks" / "user-other.json").is_file()

    # Uninstall removes only last30days hook while preserving existing user config.
    removed = hook_installer.uninstall_hooks(harness, home=tmp_path)
    assert removed["ok"] is True
    assert removed["results"][harness]["status"] == "removed"
    expected_after = "plugin-bundled" if harness == "claude" else "not-installed"
    assert hook_installer.hook_status(home=tmp_path)[harness]["status"] == expected_after

    if harness == "claude":
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert len(data["hooks"]["Stop"]) == 1
    elif harness == "antigravity":
        data = json.loads((tmp_path / ".agents" / "hooks.json").read_text(encoding="utf-8"))
        assert "user-lint" in data
        assert "last30days-brief-capture" not in data
    elif harness == "grok":
        assert not (tmp_path / ".grok" / "hooks" / "last30days-brief-capture.json").exists()
        assert (tmp_path / ".grok" / "hooks" / "user-other.json").is_file()


def test_cli_setup_install_and_uninstall_hooks_all_and_validation(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".cursor").mkdir()

    # Bare --install-hooks (no value) installs for all detected local harnesses.
    res = _run_cli("setup", "--install-hooks", home=tmp_path)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert set(payload["results"].keys()) == {"claude", "cursor"}
    assert not (tmp_path / ".codex").exists()

    # Reject unknown host.
    bad = _run_cli("setup", "--install-hooks", "unknown-host", home=tmp_path)
    assert bad.returncode == 2

    # Reject mutually exclusive flags.
    both = _run_cli("setup", "--install-hooks", "--uninstall-hooks", home=tmp_path)
    assert both.returncode == 2

    # Bare --uninstall-hooks cleans up cleanly.
    unres = _run_cli("setup", "--uninstall-hooks", home=tmp_path)
    assert unres.returncode == 0, unres.stderr
    assert json.loads(unres.stdout)["results"]["claude"]["status"] == "removed"


def test_cli_setup_install_hooks_refuses_hosted_grok_bot_honestly(tmp_path):
    res = _run_cli(
        "setup",
        "--install-hooks",
        home=tmp_path,
        extra_env={"LAST30DAYS_HOST": "grok-bot"},
    )
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["unsupported_host"] == "grok-bot"
    assert "no local" in payload["message"].lower()
    assert not (tmp_path / ".claude").exists()

    explicit = _run_cli("setup", "--install-hooks", "grok-bot", home=tmp_path)
    assert explicit.returncode == 0
    assert json.loads(explicit.stdout)["unsupported_host"] == "grok-bot"


def test_doctor_reports_brief_capture_hooks_and_finished_count_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    memory_dir = tmp_path / "Documents" / "Last30Days"
    finished = memory_dir / "finished"
    finished.mkdir(parents=True)
    (finished / "anthropic-2026-11-21.md").write_text("# stub\n", encoding="utf-8")
    hook_installer.install_hooks("cursor", home=tmp_path)

    report = doctor.build_report({"LAST30DAYS_MEMORY_DIR": str(memory_dir)})
    hooks_info = report.get("brief_capture_hooks")
    assert isinstance(hooks_info, dict)
    assert hooks_info["finished_briefs"] == 1
    assert hooks_info["harnesses"]["cursor"]["status"] == "installed"
    assert hooks_info["harnesses"]["codex"]["status"] == "not-installed"
