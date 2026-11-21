"""Install, remove, and inspect silent brief-capture hooks across local agent harnesses."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import env


SUPPORTED_HARNESSES: tuple[str, ...] = (
    "claude",
    "antigravity",
    "gemini",
    "codex",
    "cursor",
    "grok",
)
HOOK_MARKER = "capture_brief_hook.py"
ANTIGRAVITY_HOOK_NAME = "last30days-brief-capture"
GEMINI_HOOK_NAME = "last30days-brief-capture"
GROK_HOOK_FILENAME = "last30days-brief-capture.json"

_HOST_DIRS: dict[str, str] = {
    "claude": ".claude",
    "antigravity": ".agents",
    "gemini": ".gemini",
    "codex": ".codex",
    "cursor": ".cursor",
    "grok": ".grok",
}

_GROK_BOT_MESSAGE = (
    "Hosted Grok Bot has no local filesystem or hook runtime, so local "
    "brief-capture hooks cannot be installed there."
)


def hook_script_path() -> Path:
    """Return the canonical absolute path to capture_brief_hook.py."""
    return (Path(__file__).resolve().parents[1] / HOOK_MARKER).resolve()


def _home_dir(home: Path | str | None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _config_path(harness: str, home: Path) -> Path:
    if harness == "claude":
        return home / ".claude" / "settings.json"
    if harness == "antigravity":
        return home / ".agents" / "hooks.json"
    if harness == "gemini":
        return home / ".gemini" / "settings.json"
    if harness == "codex":
        return home / ".codex" / "hooks.json"
    if harness == "cursor":
        return home / ".cursor" / "hooks.json"
    if harness == "grok":
        return home / ".grok" / "hooks" / GROK_HOOK_FILENAME
    raise ValueError(f"unknown harness: {harness}")


def detect_local_harnesses(*, home: Path | str | None = None) -> list[str]:
    """Return supported local harnesses whose config root directory already exists."""
    base = _home_dir(home)
    return [name for name in SUPPORTED_HARNESSES if (base / _HOST_DIRS[name]).is_dir()]


def _command_string(script: Path) -> str:
    return f'python3 "{script}"'


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(data, indent=2) + "\n"
    staged = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    )
    try:
        with staged:
            staged.write(rendered)
        os.replace(staged.name, path)
    finally:
        Path(staged.name).unlink(missing_ok=True)


def _group_has_marker(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    if HOOK_MARKER in str(group.get("command") or ""):
        return True
    handlers = group.get("hooks")
    if isinstance(handlers, list):
        return any(
            isinstance(handler, dict) and HOOK_MARKER in str(handler.get("command") or "")
            for handler in handlers
        )
    return False


def _install_grouped_event(
    path: Path,
    *,
    event: str,
    new_group: dict[str, Any],
    extra_top: dict[str, Any] | None = None,
) -> str:
    data = _read_json_object(path)
    if extra_top:
        for key, value in extra_top.items():
            data.setdefault(key, value)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    groups = hooks.get(event)
    if not isinstance(groups, list):
        groups = []
        hooks[event] = groups

    for idx, existing in enumerate(groups):
        if _group_has_marker(existing):
            if existing == new_group:
                return "already-installed"
            groups[idx] = new_group
            _write_json_atomic(path, data)
            return "updated"

    groups.append(new_group)
    _write_json_atomic(path, data)
    return "installed"


def _uninstall_grouped_event(path: Path, *, event: str) -> str:
    if not path.is_file():
        return "not-installed"
    data = _read_json_object(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return "not-installed"
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return "not-installed"
    filtered = [group for group in groups if not _group_has_marker(group)]
    if len(filtered) == len(groups):
        return "not-installed"
    hooks[event] = filtered
    _write_json_atomic(path, data)
    return "removed"


def _install_one(harness: str, home: Path, script: Path) -> dict[str, Any]:
    path = _config_path(harness, home)
    command = _command_string(script)
    note = ""

    if harness in {"claude", "codex"}:
        group = {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
        status = _install_grouped_event(path, event="Stop", new_group=group)
        if harness == "codex":
            note = "Run /hooks inside Codex CLI to trust the newly installed hook."
    elif harness == "gemini":
        group = {
            "matcher": "*",
            "hooks": [
                {
                    "name": GEMINI_HOOK_NAME,
                    "type": "command",
                    "command": command,
                    "timeout": 5000,
                }
            ],
        }
        status = _install_grouped_event(path, event="AfterAgent", new_group=group)
    elif harness == "cursor":
        group = {"command": command}
        status = _install_grouped_event(
            path,
            event="afterAgentResponse",
            new_group=group,
            extra_top={"version": 1},
        )
    elif harness == "antigravity":
        data = _read_json_object(path)
        entry = {"Stop": [{"type": "command", "command": command, "timeout": 5}]}
        existing = data.get(ANTIGRAVITY_HOOK_NAME)
        if existing == entry:
            status = "already-installed"
        else:
            status = "updated" if existing is not None else "installed"
            data[ANTIGRAVITY_HOOK_NAME] = entry
            _write_json_atomic(path, data)
    elif harness == "grok":
        desired = {
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
                ]
            }
        }
        if path.is_file() and _read_json_object(path) == desired:
            status = "already-installed"
        else:
            status = "updated" if path.is_file() else "installed"
            _write_json_atomic(path, desired)
    else:
        raise ValueError(f"unknown harness: {harness}")

    result: dict[str, Any] = {"status": status, "path": str(path)}
    if note:
        result["note"] = note
    return result


def _uninstall_one(harness: str, home: Path) -> dict[str, Any]:
    path = _config_path(harness, home)
    if harness in {"claude", "codex"}:
        status = _uninstall_grouped_event(path, event="Stop")
    elif harness == "gemini":
        status = _uninstall_grouped_event(path, event="AfterAgent")
    elif harness == "cursor":
        status = _uninstall_grouped_event(path, event="afterAgentResponse")
    elif harness == "antigravity":
        if not path.is_file():
            status = "not-installed"
        else:
            data = _read_json_object(path)
            if ANTIGRAVITY_HOOK_NAME not in data:
                status = "not-installed"
            else:
                del data[ANTIGRAVITY_HOOK_NAME]
                _write_json_atomic(path, data)
                status = "removed"
    elif harness == "grok":
        if path.is_file():
            path.unlink()
            status = "removed"
        else:
            status = "not-installed"
    else:
        raise ValueError(f"unknown harness: {harness}")
    return {"status": status, "path": str(path)}


def _resolve_targets(target: str, home: Path) -> list[str]:
    normalized = target.strip().lower() or "all"
    if normalized == "all":
        return detect_local_harnesses(home=home)
    if normalized in SUPPORTED_HARNESSES:
        return [normalized]
    raise ValueError(
        f"unknown harness '{target}'; supported harnesses are {', '.join(SUPPORTED_HARNESSES)}, all"
    )


def _is_hosted_grok_bot(target: str, host_env: str | None) -> bool:
    resolved_host = (
        host_env if host_env is not None else os.environ.get(env.X_HOST_VAR, "")
    ).strip()
    if env.x_policy({env.X_HOST_VAR: resolved_host}).official_only:
        return True
    return target.strip().lower() == env.GROK_BOT_HOST


def install_hooks(
    target: str = "all",
    *,
    home: Path | str | None = None,
    host_env: str | None = None,
    script_path: Path | None = None,
) -> dict[str, Any]:
    """Install silent brief-capture hooks for one or all detected local harnesses."""
    if _is_hosted_grok_bot(target, host_env):
        return {
            "ok": False,
            "unsupported_host": env.GROK_BOT_HOST,
            "message": _GROK_BOT_MESSAGE,
            "results": {},
        }

    base = _home_dir(home)
    targets = _resolve_targets(target, base)
    script = (script_path or hook_script_path()).resolve()
    results = {name: _install_one(name, base, script) for name in targets}
    return {"ok": True, "script": str(script), "results": results}


def uninstall_hooks(
    target: str = "all",
    *,
    home: Path | str | None = None,
    host_env: str | None = None,
) -> dict[str, Any]:
    """Remove silent brief-capture hooks for one or all detected local harnesses."""
    if _is_hosted_grok_bot(target, host_env):
        return {
            "ok": False,
            "unsupported_host": env.GROK_BOT_HOST,
            "message": _GROK_BOT_MESSAGE,
            "results": {},
        }

    base = _home_dir(home)
    targets = _resolve_targets(target, base)
    results = {name: _uninstall_one(name, base) for name in targets}
    return {"ok": True, "results": results}


def _is_installed(harness: str, home: Path) -> bool:
    path = _config_path(harness, home)
    if not path.is_file():
        return False
    try:
        data = _read_json_object(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if harness == "antigravity":
        entry = data.get(ANTIGRAVITY_HOOK_NAME)
        return isinstance(entry, dict) and HOOK_MARKER in json.dumps(entry)
    if harness == "grok":
        return HOOK_MARKER in json.dumps(data)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    event = {
        "claude": "Stop",
        "codex": "Stop",
        "gemini": "AfterAgent",
        "cursor": "afterAgentResponse",
    }[harness]
    groups = hooks.get(event)
    return isinstance(groups, list) and any(_group_has_marker(g) for g in groups)


def hook_status(*, home: Path | str | None = None) -> dict[str, dict[str, str]]:
    """Read-only inspection of brief-capture hook installation per local harness."""
    base = _home_dir(home)
    out: dict[str, dict[str, str]] = {}
    for harness in SUPPORTED_HARNESSES:
        path = _config_path(harness, home=base)
        installed = _is_installed(harness, base)
        status = "installed" if installed else "not-installed"
        if harness == "claude" and not installed:
            repo_hook = Path(__file__).resolve().parents[4] / ".claude-plugin" / "hooks.json"
            if repo_hook.is_file():
                status = "plugin-bundled"
        out[harness] = {"status": status, "path": str(path)}
    return out
