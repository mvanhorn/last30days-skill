"""The short format checklist printed after ``--emit=compact`` stdout.

The report's format rules live in the skill's instruction files, which a host
model reads long before the engine's output arrives. This block restates the
few that decide whether the report renders correctly, as facts about the
output it follows, at the point the model starts writing.

It is command-line presentation, not evidence and not part of the canonical
output: the renderers never produce it, so the saved file, ``--emit=md``,
JSON, HTML and the MCP ``research`` tool do not carry it. It describes the
default report shape and claims no precedence over user or host instructions.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from . import env

HEADING = "## Before you write the report"

_QUOTE_POOL_HEADING = "## Top Community Comments"
_SUBJECT_POSTS_HEADING = re.compile(r"^## Posts by (@\S+)$", re.MULTILINE)
_JOBS_DIGEST_HEADING = "## Jobs Digest"
# Registers and shapes whose template has no story paragraphs or quote lines.
_REGISTERS_WITHOUT_QUOTE_LINES = frozenset({"exec"})


def _has_heading(rendered: str, heading: str) -> bool:
    return f"\n{heading}\n" in rendered


def on_claude_code(environ: Mapping[str, Any] | None = None) -> bool:
    """True when the run's environment carries Claude Code's ``CLAUDECODE``."""
    source = os.environ if environ is None else environ
    return bool(str(source.get("CLAUDECODE") or "").strip())


def build(
    rendered: str,
    *,
    comparison: bool = False,
    register: str = "default",
    environ: Mapping[str, Any] | None = None,
) -> str:
    """The checklist block for ``rendered`` compact output, or "" when the
    caller asked for none (``LAST30DAYS_NO_REPORT_CHECKLIST``)."""
    if env.report_checklist_disabled(environ):
        return ""
    story_paragraphs = not comparison and register not in _REGISTERS_WITHOUT_QUOTE_LINES
    lines = [
        "Format facts for a report written from this output. User and host instructions come first.",
        "• Line 1 is the badge line, copied as printed (`🌐 last30days · ...`), then one blank line.",
    ]
    if story_paragraphs:
        lines.append(
            "• Each story paragraph opens with a bold headline phrase: `**Headline** - text`."
        )
        pools = []
        if _has_heading(rendered, _QUOTE_POOL_HEADING):
            pools.append(f"`{_QUOTE_POOL_HEADING}`")
        subject_posts = _SUBJECT_POSTS_HEADING.search(rendered)
        if subject_posts:
            pools.append(f"`## Posts by {subject_posts.group(1)}`")
        source = (
            f" Copy the entry up to its URL from {' or '.join(pools)}."
            if pools
            else ""
        )
        lines.append(
            "• Every quoted comment or post is on its own line under its paragraph, "
            "up to six per story, never inside a paragraph: "
            '`> "quote" 🟠 u/author on r/sub · 1,315 upvotes`.'
            + source
        )
    if on_claude_code(environ):
        lines.append(
            "• `CLAUDECODE` is set: no markdown lists anywhere in the reply "
            "(write `1\\.` and `•` at line starts). In a reply that holds a list, "
            "Claude Code prints every link as raw `name (url)` text."
        )
    if not comparison:
        if _has_heading(rendered, _JOBS_DIGEST_HEADING):
            lines.append(
                f"• This output has a `{_JOBS_DIGEST_HEADING}` block: `hiring-signals.md` "
                "decides whether a Jobs Page Secret Signals section is written."
            )
        else:
            lines.append(
                f"• This output has no `{_JOBS_DIGEST_HEADING}` block, so the report has "
                "no Jobs Page Secret Signals section."
            )
    lines.append(
        "• The fenced footer (```text ... ```) is copied whole, fence included, "
        "after the body and before the invitation."
    )
    lines.append("• The report ends on the `I'm now an expert on ...` block.")
    return "\n".join(["", HEADING, "", *lines]) + "\n"
