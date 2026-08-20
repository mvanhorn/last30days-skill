"""Regression guard: the Hermes install-time scanner must find zero CRITICAL.

Hermes (NousResearch/hermes-agent, tools/skills_guard.py) blocks community-tier
installs on a `dangerous` verdict, which any single CRITICAL finding produces.
Issue #513 was caused by 14 CRITICAL false positives; the fix removed them so the
verdict is `caution` (--force installable). This test replicates the scanner's
CRITICAL-severity regexes exactly and asserts none match in the scanned subtree,
so a future edit that reintroduces a blocking pattern fails CI instead of
silently re-blocking every Hermes user.

This is a self-contained replica (no Hermes dependency). The rule regexes below
are copied verbatim from skills_guard.py's THREAT_PATTERNS; keep them in sync if
Hermes changes them. HIGH/MEDIUM findings are intentionally NOT checked here --
they do not gate the `caution` verdict (see docs/plans hermes-scan plan).
"""
from __future__ import annotations

import re
from pathlib import Path

# scan root == the skill directory (where SKILL.md lives), matching how Hermes
# resolves owner/repo -> skills/<name>/.
SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "last30days"

# CRITICAL-severity exfiltration/injection rules from skills_guard.py, verbatim.
CRITICAL_RULES = [
    (r'fetch\s*\([^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|API)', "env_exfil_fetch"),
    (r'httpx?\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)', "env_exfil_httpx"),
    (r'requests\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)', "env_exfil_requests"),
    (r'os\.environ\s*\.get\s*\(\s*["\'][^"\']*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)', "python_environ_get_secret"),
    (r'os\.getenv\s*\(\s*[^\)]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)', "python_getenv_secret"),
    (r'ENV\[.*(?:KEY|TOKEN|SECRET|PASSWORD)', "ruby_env_secret"),
    (r'do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user', "deception_hide"),
]

# Scan-root .skillignore excludes (directory prefixes + explicit files). Mirrors
# skills/last30days/.skillignore so this test scans exactly what Hermes scans.
IGNORE_DIRS = ("assets/", "agents/", "scripts/lib/vendor/")
IGNORE_FILES = {
    "scripts/build-skill.sh", "scripts/compare.sh", "scripts/evaluate_search_quality.py",
    "scripts/test_device_auth.py", "scripts/test-v1-vs-v2.sh", "scripts/verify_v3.py",
}


def _scanned_files():
    for p in SKILL_ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(SKILL_ROOT).as_posix()
        if any(rel.startswith(d) for d in IGNORE_DIRS) or rel in IGNORE_FILES:
            continue
        # binary/asset extensions the scanner skips for text rules
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".mp3", ".json"}:
            continue
        yield rel, p


def test_zero_critical_scanner_findings():
    compiled = [(re.compile(rx, re.IGNORECASE), name) for rx, name in CRITICAL_RULES]
    hits = []
    for rel, path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rx, name in compiled:
                if rx.search(line):
                    hits.append(f"{name}  {rel}:{i}  {line.strip()[:90]}")
    assert not hits, (
        "Hermes scanner CRITICAL patterns reappeared in the scanned subtree "
        "(this re-blocks every community install). Findings:\n  " + "\n  ".join(hits)
    )
