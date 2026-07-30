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


def _navigation(current: str) -> str:
    return " | ".join(
        label if label == current else f"[{label}]({path.name})"
        for label, path in READMES.items()
    )


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)


def _code_commands(text: str) -> list[str]:
    return [
        line
        for block in _code_blocks(text)
        for line in block.splitlines()
        if line and not line.lstrip().startswith("#")
    ]


def _relative_link_targets(text: str) -> set[str]:
    destinations = re.findall(r"\]\(([^)]+)\)", text)
    return {
        destination.split("#", 1)[0]
        for destination in destinations
        if destination
        and not destination.startswith(("#", "http://", "https://", "mailto:"))
    }


def _external_link_targets(text: str) -> set[str]:
    return {
        destination
        for destination in re.findall(r"\]\(([^)]+)\)", text)
        if destination.startswith(("http://", "https://"))
    }


def test_readme_translations_preserve_structure_and_commands() -> None:
    english = READMES["English"].read_text(encoding="utf-8")
    expected_code_commands = _code_commands(english)
    expected_code_fences = english.count("```")
    expected_external_links = _external_link_targets(english)
    expected_table_structure = [
        line.count("|") for line in english.splitlines() if line.startswith("|")
    ]
    expected_ordered_items = len(re.findall(r"^\d+\. ", english, flags=re.MULTILINE))

    for label, path in READMES.items():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0] == "# /last30days"
        assert lines[2] == _navigation(label)
        assert text.count("```") == expected_code_fences
        assert _code_commands(text) == expected_code_commands
        assert _external_link_targets(text) == expected_external_links
        assert [line.count("|") for line in lines if line.startswith("|")] == (
            expected_table_structure
        )
        assert len(re.findall(r"^\d+\. ", text, flags=re.MULTILINE)) == expected_ordered_items
        assert not re.search(r"(?:ZXQ|XXQ|ZZQ|ZZZ)\d", text)
        assert not re.search(r"^＃", text, flags=re.MULTILINE)
        assert not re.search(r"\]\([^)]+\)\]", text)


def test_readme_translation_relative_links_exist() -> None:
    for path in READMES.values():
        for target in _relative_link_targets(path.read_text(encoding="utf-8")):
            assert (ROOT / target).exists(), f"{path.name}: missing relative link target {target}"
