"""Regression guard: every support path SKILL.md mentions must be a file.

Hermes (NousResearch/hermes-agent, tools/skills_hub_models.py
`_referenced_support_paths`) scans SKILL.md for path-like tokens under
references/, templates/, scripts/, assets/ and examples/. When one of them is
a directory in the repo tree rather than a file, Hermes treats it like a
symlink escape and rejects the whole bundle. The user then sees "listed in the
skills-sh index, but its files no longer exist upstream" from
`hermes skills install mvanhorn/last30days-skill/skills/last30days`, which is
what kept #1502 broken after the scanner fix: SKILL.md named `scripts/lib/`
and `scripts/lib/vendor/bird-search/`.

The regexes are copied verbatim from hermes-agent 27e38935. Mention a folder
in prose without the leading support-dir prefix (for example "the `lib`
folder"), or name a file inside it.
"""
from __future__ import annotations

import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "last30days"

_ALLOWED_SUPPORT_DIRS = frozenset({"references", "templates", "scripts", "assets", "examples"})
_LOCAL_LINK_RE = re.compile(
    r"(?:\]\(|`|(?:^|[\s\"']))((?:references|templates|scripts|assets|examples)/[^\s)`\"'<>]+)", re.MULTILINE)


def _referenced_paths(text: str) -> set[str]:
    paths = set()
    for match in _LOCAL_LINK_RE.finditer(text.replace("\\", "/")):
        candidate = match.group(1).rstrip(".,;:").split("?", 1)[0].split("#", 1)[0]
        safe = candidate.strip("/")
        if safe.split("/", 1)[0] not in _ALLOWED_SUPPORT_DIRS:
            continue
        base = safe.rsplit("/", 1)[-1]
        if re.search(r"[*?<>]", safe) or not re.search(r"[A-Za-z0-9]$", base):
            continue
        paths.add(safe)
    return paths


def test_skill_md_support_paths_are_files():
    referenced = _referenced_paths((SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8"))
    assert referenced, "expected SKILL.md to reference its support files"
    not_files = sorted(p for p in referenced if (SKILL_ROOT / p).exists() and not (SKILL_ROOT / p).is_file())
    assert not not_files, (
        "SKILL.md names these as support paths but they are directories; Hermes rejects the whole "
        "install bundle for this:\n  " + "\n  ".join(not_files)
    )


def test_extractor_matches_hermes_on_directory_mentions():
    found = _referenced_paths("Bundled: `scripts/lib/` and `scripts/lib/vendor/bird-search/` and `scripts/last30days.py`")
    assert found == {"scripts/lib", "scripts/lib/vendor/bird-search", "scripts/last30days.py"}
