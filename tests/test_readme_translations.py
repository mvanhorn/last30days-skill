import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READMES = {
    "English": ROOT / "README.md",
    "Français": ROOT / "README.fr.md",
    "Deutsch": ROOT / "README.de.md",
    "Español": ROOT / "README.es.md",
    "Português (Brasil)": ROOT / "README.pt-BR.md",
    "日本語": ROOT / "README.ja.md",
    "简体中文": ROOT / "README.zh-CN.md",
}


def _relative_link_targets(text: str) -> set[str]:
    destinations = re.findall(r"\]\(([^)]+)\)", text)
    return {
        destination.split("#", 1)[0]
        for destination in destinations
        if destination
        and not destination.startswith(("#", "http://", "https://", "mailto:"))
    }


def test_readme_translations_are_individually_well_formed() -> None:
    """Translations evolve independently while the fork migrates its public copy."""
    for path in READMES.values():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines and lines[0].startswith("# ")
        assert text.count("```") % 2 == 0
        assert not re.search(r"(?:ZXQ|XXQ|ZZQ|ZZZ)\d", text)
        assert not re.search(r"^＃", text, flags=re.MULTILINE)


def test_readme_translation_relative_links_exist() -> None:
    for path in READMES.values():
        for target in _relative_link_targets(path.read_text(encoding="utf-8")):
            assert (ROOT / target).exists(), f"{path.name}: missing relative link target {target}"
