#!/usr/bin/env python3
"""Print the lockstep version string from a manifest file on stdin.

Used by .github/workflows/changelog-guard.yml so version parsing stays out of
the YAML ``run: |`` block (column-0 Python inside that block breaks Actions).

Usage:
  git show REF:path | python3 .github/scripts/read_manifest_version.py path
"""

from __future__ import annotations

import json
import re
import sys


def version_from(path: str, text: str) -> str:
    if path.endswith("pyproject.toml"):
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', text)
        return match.group(1) if match else ""
    if path.endswith("SKILL.md"):
        match = re.search(r'(?m)^version:\s*"([^"]+)"\s*$', text)
        return match.group(1) if match else ""
    if path.endswith("uv.lock"):
        match = re.search(
            r'(?ms)^\[\[package\]\]\nname = "last30days-skill"\nversion = "([^"]+)"',
            text,
        )
        return match.group(1) if match else ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc
    if path.endswith("marketplace.json"):
        plugins = data.get("plugins") or []
        return plugins[0].get("version", "") if plugins else ""
    return data.get("version", "") or ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: read_manifest_version.py PATH < manifest",
            file=sys.stderr,
        )
        return 2
    path = argv[1]
    text = sys.stdin.read()
    sys.stdout.write(version_from(path, text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
