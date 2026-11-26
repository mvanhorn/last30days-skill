"""Unit tests for finished-brief detection, atomic persistence, and corpus redaction."""

from __future__ import annotations

import os
import stat
from datetime import date
from pathlib import Path

import pytest

from lib import brief_capture, library


MODERN_BRIEF = """🌐 last30days · Anthropic · Oct 22 to Nov 21, 2026 · v4.5.0

## What I learned

Anthropic dominates developer threads this month with rapid releases and active debate over pricing and tool use.

**KEY PATTERNS from the research:**
1. Developers praise Claude Code reliability on large refactors.
2. Teams debate API rate limits during peak launches.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 12 threads │ 48 comments
├─ 𝕏 X: 18 posts │ 410 likes
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```

---
I'm now an expert on Anthropic based on the last 30 days of community discussion. Want me to dig deeper into pricing or compare against OpenAI? Just ask.
"""

LEGACY_SYNCED_BRIEF = """🌐 last30days v3.32.0 · synced 2026-11-09

# Nvidia

Blackwell rack shipments and CUDA moat discussions lead engineering forums.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 8 threads
└─ 🗓️ Oct 10 to Nov 9, 2026
---
```
"""

COMPARISON_BRIEF = """🌐 last30days · Codex vs Cursor · Oct 22 to Nov 21, 2026 · v4.5.0

# Codex vs Cursor

| Dimension | Codex | Cursor |
|---|---|---|
| Strength | Autonomous terminal runs | Fast inline editor loop |

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 14 threads
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```

I've compared Codex and Cursor across the last 30 days. Ask if you want a workflow recommendation.
"""

CLAUDE_LIST_FREE_BRIEF = """🌐 last30days · MCP servers · Oct 22 to Nov 21, 2026 · v4.5.0

Teams are standardizing around local stdio servers with strict capability scopes.

KEY PATTERNS from the research:\\
1\\. Auth and token scoping remain the top complaint.\\
2\\. Developers favor zero-config local launchers.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 10 threads
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```
"""

CORPUS_BRIEF = """🌐 last30days · Acme Strategy · Oct 22 to Nov 21, 2026 · v4.5.0

## What I learned

Public forums focus on Acme's new pricing tier.

## From your files

Internal Q4 memo notes Project Falcon launches on Dec 15 with secret margin targets.

## Key patterns

1. Customers ask for annual billing discounts.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 6 threads
├─ 📁 Your files: 2 notes
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```
"""


def test_extract_modern_brief_strips_invitation_and_parses_metadata():
    brief = brief_capture.extract_finished_brief(MODERN_BRIEF)

    assert brief is not None
    assert brief.topic == "Anthropic"
    assert brief.slug == "anthropic"
    assert brief.published_date == date(2026, 11, 21)
    assert brief.version == "4.5.0"
    assert brief.is_private is False
    assert brief.content.startswith("🌐 last30days · Anthropic · Oct 22 to Nov 21, 2026 · v4.5.0")
    assert "All agents reported back!" in brief.content
    assert "I'm now an expert" not in brief.content


def test_extract_legacy_synced_brief_uses_heading_and_synced_date():
    brief = brief_capture.extract_finished_brief(LEGACY_SYNCED_BRIEF)

    assert brief is not None
    assert brief.topic == "Nvidia"
    assert brief.slug == "nvidia"
    assert brief.published_date == date(2026, 11, 9)
    assert brief.version == "3.32.0"


def test_extract_comparison_brief_strips_comparison_invitation():
    brief = brief_capture.extract_finished_brief(COMPARISON_BRIEF)

    assert brief is not None
    assert brief.topic == "Codex vs Cursor"
    assert brief.slug == "codex-vs-cursor"
    assert brief.published_date == date(2026, 11, 21)
    assert "| Dimension | Codex | Cursor |" in brief.content
    assert "I've compared Codex and Cursor" not in brief.content


def test_extract_preserves_claude_code_list_free_hard_breaks():
    brief = brief_capture.extract_finished_brief(CLAUDE_LIST_FREE_BRIEF)

    assert brief is not None
    assert "KEY PATTERNS from the research:\\\n1\\. Auth" in brief.content
    assert "2\\. Developers favor zero-config local launchers." in brief.content


def test_extract_handles_preamble_and_rejects_partial_quoted_and_raw_dumps():
    with_preamble = "Here is your research summary:\n\n" + MODERN_BRIEF
    extracted = brief_capture.extract_finished_brief(with_preamble)
    assert extracted is not None
    assert extracted.content.startswith("🌐 last30days · Anthropic")

    # Missing footer
    assert brief_capture.extract_finished_brief("🌐 last30days · Anthropic · Oct 22 to Nov 21, 2026\n\nHalf written") is None

    # Missing badge
    assert brief_capture.extract_finished_brief("```text\n---\n✅ All agents reported back!\n---\n```") is None

    # Quoted excerpt only
    quoted = "\n".join(f"> {line}" for line in MODERN_BRIEF.splitlines())
    assert brief_capture.extract_finished_brief(quoted) is None

    # Raw evidence dump
    raw_dump = MODERN_BRIEF + "\n<!-- EVIDENCE FOR SYNTHESIS -->\nraw data\n<!-- END EVIDENCE FOR SYNTHESIS -->\n"
    assert brief_capture.extract_finished_brief(raw_dump) is None

    canonical_dump = MODERN_BRIEF + "\n---\n# END OF last30days CANONICAL OUTPUT\n"
    assert brief_capture.extract_finished_brief(canonical_dump) is None


def test_save_finished_brief_is_idempotent_and_suffixes_distinct_content(tmp_path):
    brief = brief_capture.extract_finished_brief(MODERN_BRIEF)
    assert brief is not None

    first = brief_capture.save_finished_brief(brief, tmp_path)
    assert first.status == "saved"
    assert first.path == tmp_path / "finished" / "anthropic-2026-11-21.md"
    assert first.path.read_text(encoding="utf-8") == brief.content
    if os.name == "posix":
        assert stat.S_IMODE(first.path.stat().st_mode) == 0o644

    # Repeating identical brief is an idempotent no-op.
    second = brief_capture.save_finished_brief(brief, tmp_path)
    assert second.status == "duplicate"
    assert second.path == first.path
    assert sorted((tmp_path / "finished").glob("*.md")) == [first.path]

    # Distinct content on the same topic and date gets a numeric suffix.
    updated_text = MODERN_BRIEF.replace("active debate over pricing", "new benchmark results")
    updated_brief = brief_capture.extract_finished_brief(updated_text)
    assert updated_brief is not None
    third = brief_capture.save_finished_brief(updated_brief, tmp_path)
    assert third.status == "saved"
    assert third.path == tmp_path / "finished" / "anthropic-2026-11-21-2.md"

    # Re-saving the second variant also deduplicates cleanly.
    fourth = brief_capture.save_finished_brief(updated_brief, tmp_path)
    assert fourth.status == "duplicate"
    assert fourth.path == third.path


def test_corpus_brief_uses_private_permissions_and_fails_closed_for_public_output(tmp_path):
    brief = brief_capture.extract_finished_brief(CORPUS_BRIEF)
    assert brief is not None
    assert brief.is_private is True

    result = brief_capture.save_finished_brief(brief, tmp_path)
    assert result.status == "saved"
    if os.name == "posix":
        assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(result.path.parent.stat().st_mode) == 0o700

    # Synthesized chat briefs with local corpus markers fail closed for public output.
    assert library.is_unbounded_private_brief(brief.content) is True
    assert library.public_markdown(brief.content) == ""

    # Even heading-free woven prose with only a footer `Your files` marker fails closed.
    woven = """🌐 last30days · Acme Strategy · Oct 22 to Nov 21, 2026 · v4.5.0

What I learned:

Internal Q4 memo notes Project Cobalt launches on Dec 15 alongside public forum rumors.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 6 threads
├─ 📁 Your files: 1 note
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```
"""
    woven_brief = brief_capture.extract_finished_brief(woven)
    assert woven_brief is not None
    assert woven_brief.is_private is True
    assert library.public_markdown(woven_brief.content) == ""


def test_concurrent_saves_do_not_overwrite_distinct_briefs(tmp_path):
    import concurrent.futures

    briefs = [
        brief_capture.extract_finished_brief(
            MODERN_BRIEF.replace("active debate over pricing", f"unique variant {idx}")
        )
        for idx in range(6)
    ]
    assert all(b is not None for b in briefs)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda b: brief_capture.save_finished_brief(b, tmp_path), briefs)
        )

    assert all(r.status == "saved" for r in results)
    saved_paths = {r.path for r in results}
    assert len(saved_paths) == 6
    saved_contents = {p.read_text(encoding="utf-8") for p in saved_paths}
    for idx in range(6):
        assert any(f"unique variant {idx}" in text for text in saved_contents)


def test_long_truncated_badge_recovers_full_topic_from_raw_footer(tmp_path):
    full_topic = (
        "Comparing open source local vector databases for hybrid retrieval "
        "in production agent workflows"
    )
    raw_path = tmp_path / "comparing-open-source-local-vector-databases-for-hybrid-retrieval-in-production-agent-workflows-raw.md"
    raw_path.write_text(
        f"# last30days v4.5.0: {full_topic}\n\n- Date range: 2026-10-22 to 2026-11-21\n",
        encoding="utf-8",
    )
    long_brief_text = f"""🌐 last30days · Comparing open source local vector databases for hybrid... · Oct 22 to Nov 21, 2026 · v4.5.0

What I learned:

Engineers compare Qdrant, LanceDB, and SQLite-vec for hybrid search.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 10 threads
├─ 🗓️ Oct 22 to Nov 21, 2026
└─ 📎 Raw results saved to {raw_path}
---
```
"""
    captured = brief_capture.extract_finished_brief(long_brief_text)
    assert captured is not None
    assert captured.topic == full_topic
    assert captured.slug == library.slugify(full_topic)
    assert f"<!-- last30days-topic: {full_topic} -->" in captured.content

    saved = brief_capture.save_finished_brief(captured, tmp_path)
    parsed = library.parse_markdown_file(saved.path, source_format="finished-brief")
    assert parsed.topic == full_topic
    assert parsed.slug == library.slugify(full_topic)
    # Staging temp files are cleaned up after atomic publish.
    assert list((tmp_path / "finished").glob("*.tmp")) == []


def test_finished_brief_for_a_long_topic_gets_a_short_filename(tmp_path):
    full_topic = "multi model comparison across providers and pricing tiers " * 8
    raw_path = tmp_path / "long-topic-raw.md"
    raw_path.write_text(
        f"# last30days v4.5.0: {full_topic}\n\n- Date range: 2026-10-22 to 2026-11-21\n",
        encoding="utf-8",
    )
    brief_text = f"""🌐 last30days · Multi model comparison across providers... · Oct 22 to Nov 21, 2026 · v4.5.0

What I learned:

Teams compare model pricing tiers across providers.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 10 threads
├─ 🗓️ Oct 22 to Nov 21, 2026
└─ 📎 Raw results saved to {raw_path}
---
```
"""
    captured = brief_capture.extract_finished_brief(brief_text)
    assert captured is not None
    assert captured.slug == library.slugify(full_topic)

    saved = brief_capture.save_finished_brief(captured, tmp_path)
    assert saved.status == "saved"
    assert len(saved.path.name.encode("utf-8")) < 255
    parsed = library.parse_markdown_file(saved.path, source_format="finished-brief")
    assert parsed.slug == captured.slug
    assert len(parsed.output_name.encode("utf-8")) < 255


