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


def test_hermes_skillignore_excludes_only_non_executable_media() -> None:
    """Nothing executable that ships with the skill may be hidden from the
    install-time scan -- a clean verdict must cover the whole runtime."""
    assert _skillignore_entries() == {"assets/", "agents/"}
    for entry in ("assets", "agents"):
        assert (SKILL_ROOT / entry).exists()


def test_vendored_client_is_scanned_at_both_scan_roots() -> None:
    for ignore in (ROOT / ".skillignore", ROOT / ".clawhubignore"):
        rules = [
            line.strip() for line in ignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert not any(rule.startswith("skills/last30days/scripts") for rule in rules), ignore.name


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
