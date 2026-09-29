"""Contract: SKILL.md must not expose shell/awk positional parameters to host substitution.

Claude Code rewrites `$0`, `$1`, `$2`, ... in a SKILL.md body to the skill's
argument words before the model reads the file, and that includes text inside
fenced code blocks and inline code spans. `/last30days <topic>` always arrives
with arguments, so a bare `"$1"` in the runtime preflight reached the model as
the topic's second word and every interpreter candidate was tested against that
literal string (see issue #1171).

The braced `${1}` and the awk `$(2)` field reference mean the same thing to
their interpreters and survive substitution. Both forms are therefore required
below; a bare `$<digit>` in any code context is a regression.

Prose is deliberately out of scope. A price written as `$100` in a sentence is
substituted only when the topic happens to be that many words long, and
escaping it would put a stray backslash into text the model is meant to repeat
verbatim. This test therefore scans fenced code blocks and inline code spans
only.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"

# A `$` immediately followed by a digit, not escaped by a backslash. `${1}` and
# `$(2)` both fail this pattern because the character after `$` is `{` or `(`.
BARE_POSITIONAL = re.compile(r"(?<!\\)\$\d")


def _code_spans(text: str) -> list[tuple[int, str]]:
    """Return every fenced code block and inline code span, as (offset, body)."""
    spans: list[tuple[int, str]] = []

    fence = re.compile(r"^(```+)(\S*)\n(.*?)^\1", re.S | re.M)
    for match in fence.finditer(text):
        spans.append((match.start(), match.group(3)))

    fenced_ranges = [(m.start(), m.end()) for m in fence.finditer(text)]

    def fenced(offset: int) -> bool:
        return any(start <= offset < end for start, end in fenced_ranges)

    for match in re.finditer(r"`([^`\n]+)`", text):
        if not fenced(match.start()):
            spans.append((match.start(), match.group(1)))

    return sorted(spans)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def test_skill_md_has_no_bare_positional_parameter_in_code():
    text = SKILL_MD.read_text(encoding="utf-8")
    offenders: list[str] = []

    for offset, body in _code_spans(text):
        for match in BARE_POSITIONAL.finditer(body):
            body_line = body.count("\n", 0, match.start()) + 1
            stripped = body.splitlines()[body_line - 1].strip()
            offenders.append(
                f"SKILL.md:{_line_of(text, offset + match.start())}"
                f" (code span line {body_line}): {stripped}"
            )

    assert not offenders, (
        "Bare $<digit> in a SKILL.md code block or inline code span is rewritten "
        "by host argument substitution before the model reads it. Use ${1} in "
        "shell and $(2) in awk, or escape it as \\$1.\n"
        + "\n".join(offenders)
    )


def test_runtime_preflight_uses_the_substitution_safe_braced_form():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert 'candidate="${1}"' in text
    assert 'path="${1}"' in text


def test_version_badge_awk_uses_the_substitution_safe_field_reference():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert """gsub(/"/,"",$(2)); print $(2); exit""" in text
