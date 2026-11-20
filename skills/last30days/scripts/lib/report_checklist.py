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
_PATENT_DIGEST_HEADING = "## Patent Digest"
_POLYMARKET_HEADING = "## Polymarket Markets"
_PERSON_PROFILE_HEADING = "## Person Profile"
# Registers and shapes whose template has no story paragraphs or quote lines.
_REGISTERS_WITHOUT_QUOTE_LINES = frozenset({"exec"})


# The topics the research runbook classifies as QUERY_TYPE = RECOMMENDATIONS:
# "best X", "top X", "what X should I use", "recommended X". The engine is
# handed no query type, only the topic and the plan's intent, so the topic's
# own words are the signal.
_RECOMMENDATION_TOPIC = re.compile(
    r"^\s*(?:what(?:'s| is| are)?\s+(?:the\s+)?)?(?:best|top|recommended)\b"
    r"|\bshould i (?:use|buy|get|pick|choose)\b|\brecommendations?\b",
    re.IGNORECASE,
)
# Plan intents under which a topic that opens "Top ..." or "Best ..." is a
# name or a news story, not a request for picks.
_NOT_RECOMMENDATION_INTENTS = frozenset(
    {"breaking_news", "prediction", "comparison", "factual"}
)


def is_recommendation_run(intent: str | None, *topics: str | None) -> bool:
    """Whether a run's topic asks for recommended picks."""
    if (intent or "").strip().lower() in _NOT_RECOMMENDATION_INTENTS:
        return False
    return any(_RECOMMENDATION_TOPIC.search(topic or "") for topic in topics)


def _has_heading(rendered: str, heading: str) -> bool:
    return f"\n{heading}\n" in rendered


# A detailed patent entry: a linked title whose line carries its abstract or first claim.
_PATENT_ENTRY_WITH_TEXT = re.compile(r"^- \[.*\| (?:abstract|first claim): ", re.MULTILINE)


def _patent_digest_has_text(rendered: str) -> bool:
    """True when the ``## Patent Digest`` block lists at least one filing with its text."""
    marker = f"\n{_PATENT_DIGEST_HEADING}\n"
    if marker not in rendered:
        return False
    block = rendered.split(marker, 1)[1].split("\n## ", 1)[0]
    return bool(_PATENT_ENTRY_WITH_TEXT.search(block))


def on_claude_code(environ: Mapping[str, Any] | None = None) -> bool:
    """True when the run's environment carries Claude Code's ``CLAUDECODE``."""
    source = os.environ if environ is None else environ
    return bool(str(source.get("CLAUDECODE") or "").strip())


def claude_code_in_terminal(environ: Mapping[str, Any] | None = None) -> bool:
    """True when Claude Code runs as the terminal app (``CLAUDE_CODE_ENTRYPOINT``
    is ``cli``, or unset on older builds). Only the terminal redraws links as
    ``name (url)`` while a markdown list streams; T3 Code (``sdk-ts``), the
    desktop app and IDEs render the markdown themselves."""
    source = os.environ if environ is None else environ
    entrypoint = str(source.get("CLAUDE_CODE_ENTRYPOINT") or "").strip()
    return entrypoint in ("", "cli")


def build(
    rendered: str,
    *,
    comparison: bool = False,
    recommendation: bool = False,
    register: str = "default",
    environ: Mapping[str, Any] | None = None,
) -> str:
    """The checklist block for ``rendered`` compact output, or "" when the
    caller asked for none (``LAST30DAYS_NO_REPORT_CHECKLIST``).

    Comparison, recommendation and ``exec`` reports have no story paragraphs
    with quote lines under them (a recommendation puts quoted evidence on each
    pick's ``Evidence:`` line), so they get neither of those two lines."""
    if env.report_checklist_disabled(environ):
        return ""
    story_paragraphs = (
        not comparison
        and not recommendation
        and register not in _REGISTERS_WITHOUT_QUOTE_LINES
    )
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
            " Copy the entry through its count (through the platform name when it "
            f"has no count) from {' or '.join(pools)}."
            if pools
            else ""
        )
        lines.append(
            "• The report's best six quotes each get their own line under their "
            'paragraph: `> "quote" 🟠 u/author on r/sub · 1,315 upvotes` (six in the '
            "whole report, the subject's own posts included; a ceiling, not a target). "
            "Other good quotes may stay inside a paragraph, attributed and linked, with "
            "no count; no quote is used both ways, and weak or off-topic comments are "
            "not quoted. Use each thread's or video's top comment first, then its #2; "
            "an entry ending `said` is a line said in the video itself and is a quote "
            "like any comment; like counts do not rank a comment from one place "
            "against another."
            + source
        )
        if _has_heading(rendered, _POLYMARKET_HEADING):
            lines.append(
                "• Polymarket odds for a story go on their own line under it: copy the "
                f"whole odds line from `{_POLYMARKET_HEADING}`, up to the ` — ` before its "
                "URL (at most three in the report); the paragraph links the market and "
                "does not repeat the numbers. "
                "Shape: `> 📊 Polymarket · Huang divorce: 9.5% by Dec 31 (down 1 pt today) · "
                "18% by Mar 31, 2027`. They do not count toward the six quote lines."
            )
    if on_claude_code(environ) and claude_code_in_terminal(environ):
        lines.append(
            "• `CLAUDECODE` is set in the terminal: no markdown lists anywhere in the reply "
            "(KEY PATTERNS as `1\\.`, invitation lines as `•`). Keep each block tight: "
            "end every line of the block except its last with `\\` (the label line too) and "
            "leave no blank line between them; a markdown viewer joins plain line breaks. "
            "In a reply that holds a list, "
            "Claude Code prints every link as raw `name (url)` text. This holds for the whole "
            "message, not only the report: notes you add before or after it carry no bullets or "
            "numbered lines either, and it holds again whenever you show the report in a later "
            "message."
        )
    elif on_claude_code(environ):
        lines.append(
            "• `CLAUDECODE` is set in an app that renders markdown: write KEY PATTERNS as a "
            "real numbered list and the invitation as a real bulleted list, with no blank line "
            "between items."
        )
    if not comparison:
        # One line for both digests: the checklist stays a few lines long.
        if _has_heading(rendered, _JOBS_DIGEST_HEADING):
            jobs = (
                f"This output has a `{_JOBS_DIGEST_HEADING}` block: `hiring-signals.md` "
                "decides whether a Jobs Page Secret Signals section is written."
            )
        else:
            jobs = (
                f"This output has no `{_JOBS_DIGEST_HEADING}` block, so the report has "
                "no Jobs Page Secret Signals section."
            )
        if _has_heading(rendered, _PATENT_DIGEST_HEADING):
            patents = (
                f"This output has a `{_PATENT_DIGEST_HEADING}` block: `patent-signals.md` "
                "decides whether a Patent Secret Signals section is written."
            )
            if story_paragraphs and _patent_digest_has_text(rendered):
                # The check is owed before the section is written or left out, so it is
                # stated here, outside the evidence block, not only in the reference.
                patents += (
                    " Before deciding, judge every detailed entry for something concrete a "
                    "reader can picture, from its abstract and first claim, then web-check the "
                    "two strongest concrete entries for coverage and a matching product before "
                    "dropping either; send these patent checks in the Step 2 dispatch in "
                    "addition to the three supplements. With no concrete entry, run no patent "
                    "search."
                )
        else:
            patents = (
                f"This output has no `{_PATENT_DIGEST_HEADING}` block, so the report has "
                "no Patent Secret Signals section."
            )
        digests = f"• {jobs} {patents}"
        if _has_heading(rendered, _PERSON_PROFILE_HEADING):
            # Shares the digest line: the checklist stays a few lines long.
            digests += (
                f" This output has a `{_PERSON_PROFILE_HEADING}` block: call the person quiet "
                "only when it says `LinkedIn: checked` with no posts in the window; otherwise "
                "name the sources that were not checked. Use no fact from an item that does not "
                "match the person's identity, and say so when identity is uncertain."
            )
        lines.append(digests)
    lines.append(
        "• The fenced footer (```text ... ```) is copied whole, fence included, "
        "after the body and before the invitation."
    )
    lines.append("• The report ends on the `I'm now an expert on ...` block.")
    return "\n".join(["", HEADING, "", *lines]) + "\n"
