from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "last30days"


def _skillignore_entries() -> set[str]:
    text = (SKILL_ROOT / ".skillignore").read_text(encoding="utf-8")
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_hermes_skillignore_excludes_non_runtime_scan_surface() -> None:
    entries = _skillignore_entries()

    expected = {
        "assets/",
        "agents/",
        "scripts/build-skill.sh",
        "scripts/compare.sh",
        "scripts/evaluate_search_quality.py",
        "scripts/test_device_auth.py",
        "scripts/test-v1-vs-v2.sh",
        "scripts/verify_v3.py",
    }

    assert expected <= entries
    for entry in sorted(expected):
        assert (SKILL_ROOT / entry.rstrip("/")).exists()


def test_hermes_skillignore_keeps_runtime_contract_scannable() -> None:
    entries = _skillignore_entries()

    expected_scannable = {
        "SKILL.md",
        "references",
        "references/",
        "references/save-html-brief.md",
        "scripts/last30days.py",
        "scripts/lib/",
    }

    for entry in sorted(expected_scannable):
        assert entry not in entries
        assert (SKILL_ROOT / entry.rstrip("/")).exists()
