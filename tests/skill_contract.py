"""Read runtime instructions only through their required root routing gates."""

from pathlib import Path
import re


SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "last30days"
ROUTES = {
    "Before any engine command": ("runtime",),
    "Library search, feed, or queue intent": ("library-queue",),
    "First-run setup is required": ("setup-wizard",),
    "Source repair requested or indicated by doctor": ("setup-wizard",),
    "Trending/discovery intent": ("discovery",),
    "Ordinary topic research or source-health diagnosis": ("research-runbook",),
    "Before synthesizing ordinary or comparison research": ("synthesis",),
    "Explicit comparison intent": ("comparison",),
    "--competitors mode": ("competitors", "comparison"),
    "Hiring intent or an engine ## Hiring Signals block": ("hiring-signals",),
    "--agent mode or explicit machine-readable JSON": ("agent-mode",),
    "Recommendation intent": ("recommendations",),
    "Identifiable product requires category peers": ("category-peers",),
    "Grok Bot official X connector lane": ("grok-bot-x",),
    "Follow-up on existing research": ("followups",),
    "HTML/export intent": ("save-html-brief",),
}
STANDARD_CONDITIONS = (
    "Before any engine command",
    "Ordinary topic research or source-health diagnosis",
    "Before synthesizing ordinary or comparison research",
)


def root_text(skill_root=SKILL_ROOT):
    return (skill_root / "SKILL.md").read_text(encoding="utf-8")


def routing_table(skill_root=SKILL_ROOT):
    text = root_text(skill_root)
    assert "## Reference routing\n" in text, "missing root reference routing gate"
    section = text.split("## Reference routing\n", 1)[1].split("\n## ", 1)[0]
    routes = {}
    for line in section.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0] == "Condition" or cells[0].startswith("---"):
            continue
        assert len(cells) == 3, f"malformed reference route: {line}"
        condition, instruction, return_point = cells
        condition = condition.replace("`", "")
        links = re.findall(r"\[([^]]+)\]\(([^)]+)\)", instruction)
        assert links, f"route {condition!r} has no load link"
        assert instruction.startswith("Read ") and instruction.endswith("in full."), (
            f"route {condition!r} must require reading its reference in full"
        )
        references = []
        for label, target in links:
            assert label == target, f"reference label/target mismatch: {label} -> {target}"
            assert re.fullmatch(r"references/[a-z-]+\.md", target), target
            references.append(Path(target).stem)
        assert condition not in routes, f"duplicate routing condition: {condition}"
        assert return_point, f"route {condition!r} has no return point"
        routes[condition] = tuple(references)
    return routes


def reference_text(name, skill_root=SKILL_ROOT):
    expected = {condition: names for condition, names in ROUTES.items() if name in names}
    assert expected, f"unclassified reference: {name}"
    actual = routing_table(skill_root)
    for condition, names in expected.items():
        assert actual.get(condition) == names, f"missing or incorrect {condition!r} load gate"
    path = skill_root / "references" / f"{name}.md"
    assert path.is_file(), f"required reference is missing: {path}"
    return path.read_text(encoding="utf-8")


def contract_documents(skill_root=SKILL_ROOT):
    """Global prohibitions also apply in every reachable optional instruction."""
    return {
        "SKILL.md": root_text(skill_root),
        **{name: reference_text(name, skill_root) for name in dict.fromkeys(
            name for names in ROUTES.values() for name in names
        )},
    }
