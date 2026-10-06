"""Every engine CLI flag must be reachable from SKILL.md.

AGENTS.md: "A new engine flag with no SKILL.md integration is incomplete."
This test enforces that rule: each non-suppressed long option registered by
build_parser() must appear literally in skills/last30days/SKILL.md, so the
model invoking the skill can discover every flag a user might pass through.

Suppressed (argparse.SUPPRESS-help) flags are exempt: they are hidden from
--help on purpose and must never be promoted into the runtime spec.
"""
import re
from pathlib import Path

import last30days as cli

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "skills" / "last30days" / "SKILL.md"


def _documented_flags():
    """Long options from the parser, minus hidden (--help) and suppressed."""
    flags = set()
    for action in cli.build_parser()._actions:
        if action.help == "==SUPPRESS==":
            continue
        for opt in action.option_strings:
            if opt.startswith("--") and opt != "--help":
                flags.add(opt)
    return flags


def test_all_parser_flags_documented_in_skill_md():
    skill = SKILL_MD.read_text(encoding="utf-8")
    missing = [
        flag
        for flag in sorted(_documented_flags())
        # A trailing [^-\w] lookahead keeps --deep from matching
        # --deep-research (and --days from --days-suffix style collisions)
        # while still matching --flag=value, --flag`, --flag , etc.
        if not re.search(re.escape(flag) + r"(?![-\w])", skill)
    ]
    assert not missing, (
        "engine flags missing from SKILL.md (document them or suppress "
        f"with reason in build_parser): {', '.join(missing)}"
    )
