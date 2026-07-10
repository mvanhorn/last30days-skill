"""Typed source health: classify a source/tool outcome honestly.

The pipeline historically collapsed every failure into "returned nothing" or a
flat ``errors_by_source`` entry, which hides the difference between a tool that
is *absent*, one that is *present but broken* (the classic stale-venv-shim after
a Python upgrade), one that *timed out*, and one that merely *degraded* (fewer
results than expected). This module gives callers a small typed vocabulary so
warnings can say what actually happened and prescribe the right fix.

It complements ``preflight.py`` (which gates doomed *queries*); this gates
doomed *sources/tools*.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# Health states, best to worst.
OK = "ok"
DEGRADED = "degraded"        # ran, but returned less than expected
MISSING = "missing"          # tool/binary/credential absent
BROKEN = "broken"            # present but won't execute (stale shim, bad perms)
TIMEOUT = "timeout"          # exceeded the probe deadline
ERROR = "error"              # ran and failed for another reason

# Per-run outcomes. Doctor does not emit these: it predicts source readiness
# before retrieval, while Report.source_status records what happened in one run.
NO_RESULTS = "no-results"
PARTIAL = "partial"
RATE_LIMITED = "rate-limited"
AUTH_FAILED = "auth-failed"
UNREACHABLE = "unreachable"
SCHEMA_DRIFT = "schema-drift"
SKIPPED_UNCONFIGURED = "skipped-unconfigured"


@dataclass
class SourceHealth:
    """Typed outcome for a source or the tool backing it.

    ``state`` is one of the module-level constants. ``reason`` is a short,
    human-readable explanation suitable for a run warning.
    """

    name: str
    state: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK

    @property
    def usable(self) -> bool:
        """True when the source produced something worth keeping (ok/degraded)."""
        return self.state in (OK, DEGRADED)


def probe_command(
    command: list[str],
    timeout: float = 5.0,
) -> SourceHealth:
    """Probe an external command, distinguishing missing/broken/timeout/ok.

    Separating these is what lets the caller emit a correct repair prescription
    instead of a generic "failed":
      - ``missing``: the executable is not on PATH.
      - ``broken``: on PATH but won't run — FileNotFoundError/OSError on exec, or
        shell exit 126/127 (not-executable / not-found-after-resolution), the
        signature of a stale interpreter shim after an upgrade.
      - ``timeout``: exceeded ``timeout`` seconds.
      - ``ok``: exited 0.
      - ``error``: ran but exited non-zero for another reason.

    The command should be side-effect-free (e.g. ``["gh", "auth", "status"]``);
    callers pass a status/version subcommand, not a mutating one.
    """
    name = command[0] if command else ""
    if not name or shutil.which(name) is None:
        return SourceHealth(name=name, state=MISSING, reason=f"{name or 'command'} not found on PATH")

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError) as exc:
        return SourceHealth(name=name, state=BROKEN, reason=f"{name} present but won't execute: {exc}")
    except subprocess.TimeoutExpired:
        return SourceHealth(name=name, state=TIMEOUT, reason=f"{name} timed out after {timeout:g}s")

    if proc.returncode == 0:
        return SourceHealth(name=name, state=OK)
    if proc.returncode in (126, 127):
        return SourceHealth(name=name, state=BROKEN, reason=f"{name} not executable (exit {proc.returncode})")
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    first = detail[0] if detail else f"exit {proc.returncode}"
    return SourceHealth(name=name, state=ERROR, reason=f"{name}: {first}")


# ---------------------------------------------------------------------------
# Dependency probes (doctor command, issue #692).
#
# ``probe_dependency`` generalizes ``probe_command`` for the skill's external
# binaries (yt-dlp, Printing Press CLIs, node for the vendored bird client,
# ffmpeg). It answers three questions the bare shutil.which gate cannot:
#   - Is the binary genuinely runnable (a stale shim that resolves on PATH but
#     cannot exec is BROKEN, not available)?
#   - If not, WHICH fix applies (install vs reinstall vs a PATH edit), keyed to
#     the package manager that owns the binary on this machine?
#   - Is an on-disk binary merely off the agent-subprocess PATH (the Digg
#     ~/.local/bin case) — MISSING with a PATH-fix, never "installed"?
#
# Semantics follow the engine gate: availability means PATH-resolvable in THIS
# process, not present-on-disk. Probes are one short-timeout version exec each
# and memoized per process, so doctor and setup can consult them freely.
# ---------------------------------------------------------------------------

# Per-probe budget in seconds: a healthy --version exec is near-instant, so a
# slow probe is itself a diagnostic (network-mounted shim, hung interpreter).
PROBE_TIMEOUT = 5.0

_PP_CLI_SUFFIX = "-pp-cli"
# Matches setup_wizard.PRINTING_PRESS_NPM (pinned catalog installer).
_PRINTING_PRESS_NPM = "@mvanhorn/printing-press-library@0.1.16"

# Dependencies the doctor probes by default.
KNOWN_DEPENDENCIES: Tuple[str, ...] = ("yt-dlp", "digg-pp-cli", "node", "ffmpeg")

# Cheap side-effect-free version invocation per dependency (default --version).
_VERSION_ARGS: Dict[str, List[str]] = {
    "ffmpeg": ["-version"],
}

# Package managers each dependency may be owned by, in preference order, and
# the (install, reinstall) prescription for each. "reinstall" wording matters:
# a BROKEN binary is present, so telling the user to "install" it reads as a
# no-op ("it's already installed") — the stale-shim trap this module exists
# to name.
_MANAGER_PRESCRIPTIONS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "yt-dlp": {
        "brew": ("brew install yt-dlp", "brew reinstall yt-dlp"),
        "pipx": ("pipx install yt-dlp", "pipx reinstall yt-dlp"),
    },
    "node": {
        "brew": ("brew install node", "brew reinstall node"),
        "nvm": ("nvm install --lts", "reinstall node via nvm: nvm install --lts && nvm use --lts"),
    },
    "ffmpeg": {
        "brew": ("brew install ffmpeg", "brew reinstall ffmpeg"),
        "apt": ("sudo apt-get install -y ffmpeg", "sudo apt-get install -y --reinstall ffmpeg"),
    },
}

# Last-resort prescriptions when no known package manager is detected.
_FALLBACK_PRESCRIPTIONS: Dict[str, Tuple[str, str]] = {
    "yt-dlp": (
        "install yt-dlp (https://github.com/yt-dlp/yt-dlp#installation) and ensure it is on PATH",
        "reinstall yt-dlp (https://github.com/yt-dlp/yt-dlp#installation); the current binary won't run",
    ),
    "node": (
        "install Node.js 22+ (https://nodejs.org) and ensure `node` is on PATH",
        "reinstall Node.js 22+ (https://nodejs.org); the current binary won't run",
    ),
    "ffmpeg": (
        "install ffmpeg (https://ffmpeg.org/download.html) and ensure it is on PATH",
        "reinstall ffmpeg (https://ffmpeg.org/download.html); the current binary won't run",
    ),
}


@dataclass
class DependencyProbe:
    """Uniform probe result for one external dependency.

    ``status`` is one of the module-level constants (OK/MISSING/BROKEN/TIMEOUT).
    ``detail`` says what was observed (version string, exec error, off-PATH
    location). ``prescription`` is the copy-pasteable fix, empty when OK.
    ``owner_pkg_manager`` names the manager the prescription targets
    ("brew", "pipx", "apt", "nvm", "npx"), or "" for PATH fixes / fallbacks.
    """

    name: str
    status: str
    detail: str = ""
    prescription: str = ""
    owner_pkg_manager: str = ""
    # True for the on-disk-but-off-PATH case: MISSING (the engine gate would
    # not pass) but the fix is a PATH edit, not an install.
    off_path: bool = False

    @property
    def ok(self) -> bool:
        return self.status == OK


# Safe under the GIL (dict get/set are atomic) and each dependency name is
# probed from a single builder today; worst case is one redundant probe.
_dependency_probe_cache: Dict[str, DependencyProbe] = {}


def clear_dependency_probe_cache() -> None:
    """Reset memoized probes (tests, or a doctor re-run after a fix)."""
    _dependency_probe_cache.clear()


def _nvm_present() -> bool:
    return bool(os.environ.get("NVM_DIR")) or (Path.home() / ".nvm").is_dir()


def _manager_available(manager: str) -> bool:
    if manager == "nvm":
        return _nvm_present()
    if manager == "apt":
        return shutil.which("apt-get") is not None
    return shutil.which(manager) is not None


def _is_pp_cli(name: str) -> bool:
    return name.endswith(_PP_CLI_SUFFIX) and len(name) > len(_PP_CLI_SUFFIX)


def _pp_install_cmd(name: str) -> str:
    slug = name[: -len(_PP_CLI_SUFFIX)]
    return f"npx -y {_PRINTING_PRESS_NPM} install {slug} --cli-only"


def pp_install_cmd(slug: str) -> str:
    """Public catalog-install command for the Printing Press CLI ``<slug>-pp-cli``."""
    return _pp_install_cmd(f"{slug}{_PP_CLI_SUFFIX}")


def static_prescription(name: str, manager: str) -> Tuple[str, str]:
    """Public ``(install, reinstall)`` strings for one dependency/manager pair.

    Reads the static table without probing manager availability; raises
    KeyError for unknown pairs so consumers fail loudly at import time.
    """
    return _MANAGER_PRESCRIPTIONS[name][manager]


def _prescription(name: str, kind: str) -> Tuple[str, str]:
    """Return ``(prescription, owner_pkg_manager)`` for install/reinstall.

    ``kind`` is "install" (MISSING) or "reinstall" (BROKEN). Printing Press
    CLIs always re-run the catalog installer; other deps pick the first
    detected manager from their preference table, falling back to a generic
    but still actionable instruction.
    """
    idx = 0 if kind == "install" else 1
    if _is_pp_cli(name):
        cmd = _pp_install_cmd(name)
        if kind == "reinstall":
            return f"re-run the Printing Press install: {cmd}", "npx"
        return cmd, "npx"
    for manager, prescriptions in _MANAGER_PRESCRIPTIONS.get(name, {}).items():
        if _manager_available(manager):
            return prescriptions[idx], manager
    fallback = _FALLBACK_PRESCRIPTIONS.get(name)
    if fallback:
        return fallback[idx], ""
    verb = "install" if kind == "install" else "reinstall"
    return f"{verb} {name} and ensure it is on PATH", ""


def windows_printing_press_bin_dir() -> Optional[Path]:
    """Windows managed install dir for Printing Press CLIs, when applicable.

    Returns ``%LOCALAPPDATA%/Programs/PrintingPress/bin`` on Windows when
    LOCALAPPDATA is set; ``None`` otherwise.
    """
    if os.name != "nt":
        return None
    local_app = os.environ.get("LOCALAPPDATA") or os.environ.get("LocalAppData")
    if not local_app:
        return None
    return Path(local_app) / "Programs" / "PrintingPress" / "bin"


def installer_bin_dirs() -> List[Path]:
    """Installer-managed bin dirs shared with setup_wizard's Digg candidates.

    Single source of truth for where installers drop binaries: the Printing
    Press library default (~/.local/bin), Go bins, and — on Windows — the
    managed %LOCALAPPDATA%/Programs/PrintingPress/bin dir.
    ``setup_wizard._digg_bin_candidate_paths`` derives its Digg-specific
    paths from this list; keep the two in lockstep by editing only here.
    """
    home = Path.home()
    dirs = [home / ".local" / "bin"]
    gopath = os.environ.get("GOPATH")
    if gopath:
        dirs.append(Path(gopath) / "bin")
    dirs.append(home / "go" / "bin")
    win_dir = windows_printing_press_bin_dir()
    if win_dir is not None:
        dirs.append(win_dir)
    return dirs


def _off_path_candidate_dirs() -> List[Path]:
    """Directories where installers drop binaries that PATH may not cover.

    The shared installer dirs (``installer_bin_dirs``, which also backs
    setup_wizard's Digg candidates) plus the Homebrew prefixes (an agent
    subprocess PATH sometimes omits even those).
    """
    dirs = installer_bin_dirs()
    dirs.extend([Path("/opt/homebrew/bin"), Path("/usr/local/bin")])
    return dirs


def _off_path_binary(name: str) -> Optional[Path]:
    """Return an executable for ``name`` in a known dir that PATH misses."""
    names = [name, f"{name}.exe"] if os.name == "nt" else [name]
    for directory in _off_path_candidate_dirs():
        for candidate_name in names:
            candidate = directory / candidate_name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _path_hint(directory: Path) -> str:
    """Render a bin dir with $HOME substituted for copy-pasteable PATH edits."""
    raw = str(directory)
    if os.name == "nt":
        return raw
    home = str(Path.home())
    if raw == home:
        return "$HOME"
    if raw.startswith(home + os.sep):
        return "$HOME/" + raw[len(home) + 1:].replace(os.sep, "/")
    return raw


def probe_dependency(name: str, timeout: float = PROBE_TIMEOUT) -> DependencyProbe:
    """Probe one external dependency: OK | MISSING | BROKEN | TIMEOUT.

    - MISSING: not resolvable on this process's PATH. If the binary exists in
      a known install dir, the prescription is a PATH edit, not an install —
      installing again would not fix anything.
    - BROKEN: shutil.which resolves it but a cheap version exec fails
      (OSError/exec-format, or any non-zero exit). Prescription says
      *reinstall* — the #692 stale-shim class must never read as available.
    - TIMEOUT: the version exec exceeded the per-probe budget.
    - OK: version exec exited 0; ``detail`` carries the version line.

    Memoized per process; ``clear_dependency_probe_cache()`` resets.
    """
    cached = _dependency_probe_cache.get(name)
    if cached is not None:
        return cached
    probe = _probe_dependency_uncached(name, timeout)
    _dependency_probe_cache[name] = probe
    return probe


def _probe_dependency_uncached(name: str, timeout: float) -> DependencyProbe:
    resolved = shutil.which(name)
    if resolved is None:
        off_path = _off_path_binary(name)
        if off_path is not None:
            hint = _path_hint(off_path.parent)
            return DependencyProbe(
                name=name,
                status=MISSING,
                detail=f"{name} is installed at {off_path} but that directory is not on this process's PATH",
                prescription=f'add {hint} to PATH (e.g. export PATH="{hint}:$PATH") so {name} resolves',
                owner_pkg_manager="",
                off_path=True,
            )
        prescription, manager = _prescription(name, "install")
        return DependencyProbe(
            name=name,
            status=MISSING,
            detail=f"{name} not found on PATH",
            prescription=prescription,
            owner_pkg_manager=manager,
        )

    command = [name] + _VERSION_ARGS.get(name, ["--version"])
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError) as exc:
        prescription, manager = _prescription(name, "reinstall")
        return DependencyProbe(
            name=name,
            status=BROKEN,
            detail=f"{name} resolves to {resolved} but won't execute: {exc}",
            prescription=prescription,
            owner_pkg_manager=manager,
        )
    except subprocess.TimeoutExpired:
        prescription, manager = _prescription(name, "reinstall")
        return DependencyProbe(
            name=name,
            status=TIMEOUT,
            detail=f"{name} version probe timed out after {timeout:g}s",
            prescription=f"re-run doctor; if the timeout persists: {prescription}",
            owner_pkg_manager=manager,
        )

    if proc.returncode == 0:
        lines = (proc.stdout or proc.stderr or "").strip().splitlines()
        version = lines[0].strip() if lines else ""
        return DependencyProbe(name=name, status=OK, detail=version)

    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    why = lines[0].strip() if lines else f"exit {proc.returncode}"
    prescription, manager = _prescription(name, "reinstall")
    return DependencyProbe(
        name=name,
        status=BROKEN,
        detail=f"{name} resolves to {resolved} but the version probe failed: {why}",
        prescription=prescription,
        owner_pkg_manager=manager,
    )


def probe_dependencies(names: Optional[Iterable[str]] = None) -> Dict[str, DependencyProbe]:
    """Probe every known dependency (or ``names``), memoized per process."""
    return {name: probe_dependency(name) for name in (names or KNOWN_DEPENDENCIES)}
