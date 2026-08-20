#!/usr/bin/env python3
"""Prepare a lockstep release: towncrier changelog + bump every version surface.

Usage (from repo root):
  python3 .github/scripts/prepare_release.py --bump patch
  python3 .github/scripts/prepare_release.py --version 3.19.0
  python3 .github/scripts/prepare_release.py --bump minor --dry-run

Do not edit CHANGELOG.md or version manifests in feature PRs — add a
changelog.d/ fragment instead. This script is for release PRs only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"

JSON_VERSION_FILES = (
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".codex-plugin" / "plugin.json",
    ROOT / ".grok-plugin" / "plugin.json",
    ROOT / "gemini-extension.json",
)

MARKETPLACE_FILES = (
    ROOT / ".claude-plugin" / "marketplace.json",
    ROOT / ".grok-plugin" / "marketplace.json",
)

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_PYPROJECT_VERSION_RE = re.compile(
    r'^(version\s*=\s*")([^"]+)(")\s*$', re.MULTILINE
)
_SKILL_FRONTMATTER_VERSION_RE = re.compile(
    r'^(version:\s*")([^"]+)(")\s*$', re.MULTILINE
)
_SKILL_HEADER_RE = re.compile(
    r"^(# last30days v)(\d+\.\d+\.\d+)(:)", re.MULTILINE
)
_UV_LOCK_PACKAGE_RE = re.compile(
    r'(?ms)^(\[\[package\]\]\nname = "last30days-skill"\nversion = ")([^"]+)(")'
)


def _parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(text.strip())
    if not match:
        raise SystemExit(f"Invalid semver (expected X.Y.Z): {text!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _format_version(parts: tuple[int, int, int]) -> str:
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def read_current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = _PYPROJECT_VERSION_RE.search(text)
    if not match:
        raise SystemExit("Could not find [project].version in pyproject.toml")
    return match.group(2)


def next_version(current: str, bump: str) -> str:
    major, minor, patch = _parse_version(current)
    if bump == "major":
        return _format_version((major + 1, 0, 0))
    if bump == "minor":
        return _format_version((major, minor + 1, 0))
    if bump == "patch":
        return _format_version((major, minor, patch + 1))
    raise SystemExit(f"Unknown bump kind: {bump!r}")


def _replace_once(path: Path, pattern: re.Pattern[str], new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = pattern.subn(rf"\g<1>{new}\g<3>", text, count=1)
    if count != 1:
        raise SystemExit(f"{path.relative_to(ROOT)}: expected one {label} match, found {count}")
    path.write_text(updated, encoding="utf-8")


def bump_pyproject(version: str) -> None:
    _replace_once(PYPROJECT, _PYPROJECT_VERSION_RE, version, "version")


def bump_skill_md(version: str) -> None:
    text = SKILL_MD.read_text(encoding="utf-8")
    text2, n1 = _SKILL_FRONTMATTER_VERSION_RE.subn(
        rf"\g<1>{version}\g<3>", text, count=1
    )
    text3, n2 = _SKILL_HEADER_RE.subn(rf"\g<1>{version}\g<3>", text2, count=1)
    if n1 != 1 or n2 != 1:
        raise SystemExit(
            f"SKILL.md: expected one frontmatter version and one H1 version, "
            f"found frontmatter={n1} header={n2}"
        )
    SKILL_MD.write_text(text3, encoding="utf-8")


def bump_json_version(path: Path, version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "version" not in data:
        raise SystemExit(f"{path.relative_to(ROOT)}: missing top-level version")
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def bump_marketplace(path: Path, version: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    plugins = data.get("plugins") or []
    if not plugins:
        raise SystemExit(f"{path.relative_to(ROOT)}: plugins[] is empty")
    plugins[0]["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def bump_uv_lock(version: str) -> None:
    text = UV_LOCK.read_text(encoding="utf-8")
    updated, count = _UV_LOCK_PACKAGE_RE.subn(rf"\g<1>{version}\g<3>", text, count=1)
    if count != 1:
        raise SystemExit(f"uv.lock: expected one last30days-skill package stanza, found {count}")
    UV_LOCK.write_text(updated, encoding="utf-8")


def run_towncrier(version: str, *, dry_run: bool) -> None:
    cmd = [
        sys.executable,
        "-m",
        "towncrier",
        "build",
        "--version",
        version,
        "--yes",
    ]
    if dry_run:
        cmd.append("--draft")
    subprocess.run(cmd, cwd=ROOT, check=True)


def bump_all(version: str) -> list[str]:
    touched: list[str] = []
    bump_pyproject(version)
    touched.append(str(PYPROJECT.relative_to(ROOT)))
    bump_skill_md(version)
    touched.append(str(SKILL_MD.relative_to(ROOT)))
    for path in JSON_VERSION_FILES:
        bump_json_version(path, version)
        touched.append(str(path.relative_to(ROOT)))
    for path in MARKETPLACE_FILES:
        bump_marketplace(path, version)
        touched.append(str(path.relative_to(ROOT)))
    bump_uv_lock(version)
    touched.append(str(UV_LOCK.relative_to(ROOT)))
    return touched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bump", choices=("major", "minor", "patch"))
    group.add_argument("--version", help="Explicit X.Y.Z to set")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned version and towncrier draft; do not write files",
    )
    parser.add_argument(
        "--skip-towncrier",
        action="store_true",
        help="Only bump version surfaces (changelog already prepared)",
    )
    args = parser.parse_args(argv)

    current = read_current_version()
    version = args.version or next_version(current, args.bump)
    _parse_version(version)
    if args.version:
        parsed_new = _parse_version(version)
        parsed_cur = _parse_version(current)
        if parsed_new < parsed_cur:
            raise SystemExit(f"Refusing to downgrade {current} → {version}")
        if parsed_new == parsed_cur and not args.dry_run:
            raise SystemExit(
                f"Refusing to re-release {current}; pass --bump or a newer --version "
                "(use --dry-run to preview towncrier output for the current version)"
            )

    print(f"Current version: {current}")
    print(f"Next version:    {version}")

    if args.dry_run:
        if not args.skip_towncrier:
            run_towncrier(version, dry_run=True)
        print("Dry run only — no files written.")
        return 0

    if not args.skip_towncrier:
        run_towncrier(version, dry_run=False)
        print("Updated CHANGELOG.md via towncrier")

    touched = bump_all(version)
    print("Bumped lockstep files:")
    for path in touched:
        print(f"  - {path}")
    print(f"\nNext: open a release PR, merge, then tag v{version} (tag-release workflow).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
