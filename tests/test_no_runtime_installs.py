"""Contract: the skill never downloads and runs third-party code.

The engine may auto-install only well-known packages from standard package
managers (today: ``brew install yt-dlp``). It must never spawn npm/npx,
``go install``, ``git clone``, ``curl | sh``, or any other installer, and the
model-facing instructions must never tell the model to run one. Printing Press
CLIs are built only by the USER via ``scripts/build-pp-cli.sh`` (pinned fork
commit), which the engine merely prints.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "last30days"
SCRIPTS = SKILL / "scripts"

# Binaries that fetch or install code when spawned.
INSTALLERS = {"npx", "npm", "pnpm", "yarn", "pip", "pip3", "pipx", "uv", "go", "git", "curl", "wget", "brew"}
# argv prefixes the engine is allowed to spawn: well-known packages from
# standard package managers only.
ALLOWED_PREFIXES = {("brew", "install", "yt-dlp")}
_GIT_NETWORK = {"clone", "fetch", "pull", "submodule", "remote", "ls-remote"}


def _engine_sources() -> list[Path]:
    return sorted(
        p for p in SCRIPTS.rglob("*.py")
        if "vendor" not in p.parts and "__pycache__" not in p.parts
    )


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def _argv_offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        head = _literal(node.elts[0])
        if head is None:
            continue
        name = Path(head).name.lower()
        if name not in INSTALLERS:
            continue
        literal = tuple(
            v for v in (_literal(e) for e in node.elts[:3]) if v is not None
        )
        if literal[:3] in ALLOWED_PREFIXES:
            continue
        # Local git plumbing (worktree, rev-parse, ...) fetches nothing.
        if name == "git" and (len(literal) < 2 or literal[1] not in _GIT_NETWORK):
            continue
        # A plain list of names (e.g. a docs table) is not argv; argv lists
        # carry at least one flag/subcommand after the binary.
        if len(node.elts) < 2:
            continue
        found.append(f"{path.relative_to(ROOT)}:{node.lineno}: {literal}")
    return found


def test_engine_spawns_no_installers():
    offenders = [o for p in _engine_sources() for o in _argv_offenders(p)]
    assert offenders == [], "engine must not spawn installers:\n" + "\n".join(offenders)


def test_allowed_auto_install_is_only_ytdlp_via_brew():
    text = (SCRIPTS / "lib" / "setup_wizard.py").read_text()
    assert '["brew", "install", "yt-dlp"]' in text


_MODEL_DOCS = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
_FORBIDDEN_DOC_PATTERNS = [
    re.compile(r"\bnpx\s+(-y\s+)?@"),            # npx-run packages
    re.compile(r"printing-press-library@"),        # the upstream installer
    re.compile(r"\|\s*(ba|z)?sh\b"),               # curl | sh / | bash
    re.compile(r"\bgo install\b"),
]


@pytest.mark.parametrize("doc", _MODEL_DOCS, ids=lambda p: p.name)
def test_model_instructions_never_run_downloaded_code(doc):
    text = doc.read_text()
    hits = [p.pattern for p in _FORBIDDEN_DOC_PATTERNS if p.search(text)]
    assert hits == [], f"{doc.name} instructs a download-and-run: {hits}"


def test_build_script_is_pinned_to_reviewed_fork():
    script = (SCRIPTS / "build-pp-cli.sh").read_text()
    repo = re.search(r'^PP_FORK_REPO="([^"]+)"', script, re.M)
    sha = re.search(r'^PP_FORK_SHA="([0-9a-f]+)"', script, re.M)
    assert repo and repo.group(1) == "https://github.com/missterr/printing-press-library.git"
    assert sha and len(sha.group(1)) == 40
    assert "GOTOOLCHAIN=local" in script
    assert "-mod=readonly" in script
    assert "does not match pin" in script
    # Executable lines must never consult the upstream registry or npm.
    code = [ln for ln in script.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("npx" in ln or "registry.json" in ln or "mvanhorn" in ln for ln in code)
