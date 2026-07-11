"""Named audience registers for standard research brief synthesis."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


SectionName = str


@dataclass(frozen=True)
class AudienceRegister:
    """A bounded renderer/synthesis preset for one intended audience."""

    name: str
    section_order: tuple[SectionName, ...]
    item_budgets: Mapping[SectionName, int]
    emphasis_weights: Mapping[str, float]

    def budget_for(self, section: SectionName, fallback: int) -> int:
        return self.item_budgets.get(section, fallback)

    def emphasis_for(self, source: str) -> float:
        return self.emphasis_weights.get(source, 1.0)


_DEFAULT_ORDER = (
    "hiring_signals",
    "clusters",
    "stats",
    "best_takes",
    "top_comments",
    "source_outcomes",
    "source_coverage",
)


def _preset(
    name: str,
    *,
    section_order: tuple[SectionName, ...] = _DEFAULT_ORDER,
    item_budgets: Mapping[SectionName, int] | None = None,
    emphasis_weights: Mapping[str, float] | None = None,
) -> AudienceRegister:
    return AudienceRegister(
        name=name,
        section_order=section_order,
        item_budgets=MappingProxyType(dict(item_budgets or {})),
        emphasis_weights=MappingProxyType(dict(emphasis_weights or {})),
    )


_REGISTERS = {
    "default": _preset("default"),
    "exec": _preset(
        "exec",
        section_order=(
            "stats",
            "clusters",
            "hiring_signals",
            "source_outcomes",
            "source_coverage",
            "best_takes",
            "top_comments",
        ),
        item_budgets={"clusters": 5, "best_takes": 2, "top_comments": 3},
        emphasis_weights={
            "polymarket": 1.50,
            "jobs": 1.30,
            "github": 1.20,
            "grounding": 1.10,
        },
    ),
    "dev": _preset(
        "dev",
        section_order=(
            "clusters",
            "source_outcomes",
            "source_coverage",
            "hiring_signals",
            "stats",
            "top_comments",
            "best_takes",
        ),
        item_budgets={"clusters": 10, "best_takes": 3, "top_comments": 4},
        emphasis_weights={
            "github": 1.60,
            "hackernews": 1.35,
            "arxiv": 1.30,
            "grounding": 1.10,
        },
    ),
    "creator": _preset(
        "creator",
        section_order=(
            "best_takes",
            "top_comments",
            "stats",
            "clusters",
            "hiring_signals",
            "source_outcomes",
            "source_coverage",
        ),
        item_budgets={"clusters": 6, "best_takes": 5, "top_comments": 8},
        emphasis_weights={
            "tiktok": 1.60,
            "instagram": 1.50,
            "youtube": 1.40,
            "x": 1.20,
            "reddit": 1.10,
        },
    ),
    # ELI5 historically changed only the agent's prose. Keep the renderer
    # descriptor identical to default and express its voice in SKILL.md.
    "eli5": _preset("eli5"),
}

REGISTER_NAMES = tuple(_REGISTERS)


def get_register(name: str | None = None) -> AudienceRegister:
    """Return a named register, rejecting unsupported/free-form templates."""

    normalized = (name or "default").strip().lower()
    try:
        return _REGISTERS[normalized]
    except KeyError as exc:
        choices = ", ".join(REGISTER_NAMES)
        raise ValueError(
            f"unknown audience register {name!r}; choose one of: {choices}"
        ) from exc
