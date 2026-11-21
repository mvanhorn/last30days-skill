"""Unit and CLI subprocess tests for offline `library harvest` transcript backfill."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path

from lib import transcript_harvest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_SCRIPT = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"

BRIEF_ANTHROPIC = """🌐 last30days · Anthropic · Oct 22 to Nov 21, 2026 · v4.5.0

## What I learned

Anthropic developer threads focus on tool use and reliability.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 12 threads
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```

---
I'm now an expert on Anthropic. Just ask.
"""

BRIEF_LEGACY_NVIDIA = """🌐 last30days v3.32.0 · synced 2026-11-09

# Nvidia

Blackwell racks dominate infrastructure threads.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 8 threads
└─ 🗓️ Oct 10 to Nov 9, 2026
---
```
"""

BRIEF_CODEX_STRIPE = """🌐 last30days · Stripe · Oct 20 to Nov 19, 2026 · v4.5.0

## What I learned

Developers debate Stripe Billing migration ergonomics.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 9 threads
└─ 🗓️ Oct 20 to Nov 19, 2026
---
```
"""

BRIEF_AG_CURSOR = """🌐 last30days · Cursor · Oct 21 to Nov 20, 2026 · v4.5.0

## What I learned

Cursor teams share hook workflows for local lint and logging.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 7 threads
└─ 🗓️ Oct 21 to Nov 20, 2026
---
```
"""

BRIEF_GEMINI_CORPUS = """🌐 last30days · Internal Launch · Oct 22 to Nov 21, 2026 · v4.5.0

## What I learned

Public market chatter is quiet.

## From your files

Private Q4 roadmap doc mentions codename Orion.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 2 threads
├─ 📁 Your files: 1 note
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```
"""


def _seed_transcripts(home: Path) -> None:
    # Claude Code JSONL (modern + legacy + corrupt line + non-brief line)
    claude_dir = home / ".claude" / "projects" / "demo-project"
    claude_dir.mkdir(parents=True)
    (claude_dir / "session-1.jsonl").write_text(
        "\n".join(
            [
                "{broken json line",
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-11-21T10:00:00Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": BRIEF_ANTHROPIC}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-11-09T10:00:00Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": BRIEF_LEGACY_NVIDIA}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {"role": "assistant", "content": "Unrelated chat reply."},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # Codex CLI rollout JSONL
    codex_dir = home / ".codex" / "sessions" / "2026" / "11" / "19"
    codex_dir.mkdir(parents=True)
    (codex_dir / "rollout-1.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-11-19T12:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": BRIEF_CODEX_STRIPE}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Antigravity transcript_full.jsonl (preferred over truncated sibling transcript.jsonl)
    ag_dir = (
        home
        / ".t3"
        / "userdata"
        / "providers"
        / "antigravity"
        / "inst1"
        / "brain"
        / "conv1"
        / ".system_generated"
        / "logs"
    )
    ag_dir.mkdir(parents=True)
    (ag_dir / "transcript.jsonl").write_text(
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "content": BRIEF_AG_CURSOR[:80],
                "truncated_fields": ["content"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (ag_dir / "transcript_full.jsonl").write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "content": BRIEF_AG_CURSOR}) + "\n",
        encoding="utf-8",
    )

    # Gemini CLI session JSON (with private corpus section)
    gem_dir = home / ".gemini" / "tmp" / "proj1" / "chats"
    gem_dir.mkdir(parents=True)
    (gem_dir / "session-2026-11-21.json").write_text(
        json.dumps(
            {
                "messages": [
                    {"type": "user", "content": "/last30days Internal Launch"},
                    {"type": "gemini", "content": BRIEF_GEMINI_CORPUS},
                ]
            }
        ),
        encoding="utf-8",
    )


def test_harvest_extracts_all_harnesses_and_is_idempotent(tmp_path):
    _seed_transcripts(tmp_path)
    save_dir = tmp_path / "memory"

    first = transcript_harvest.harvest_transcripts(save_dir=save_dir, home=tmp_path)
    assert first.saved == 5
    assert first.duplicates == 0
    finished = save_dir / "finished"
    names = sorted(p.name for p in finished.glob("*.md"))
    assert names == [
        "anthropic-2026-11-21.md",
        "cursor-2026-11-20.md",
        "internal-launch-2026-11-21.md",
        "nvidia-2026-11-09.md",
        "stripe-2026-11-19.md",
    ]
    if os.name == "posix":
        assert stat.S_IMODE((finished / "internal-launch-2026-11-21.md").stat().st_mode) == 0o600

    second = transcript_harvest.harvest_transcripts(save_dir=save_dir, home=tmp_path)
    assert second.saved == 0
    assert second.duplicates == 5


def test_harvest_dry_run_since_and_source_filtering(tmp_path):
    _seed_transcripts(tmp_path)
    save_dir = tmp_path / "memory"

    dry = transcript_harvest.harvest_transcripts(
        save_dir=save_dir,
        source="claude",
        since=date(2026, 11, 15),
        dry_run=True,
        home=tmp_path,
    )
    assert dry.dry_run is True
    assert dry.saved == 1
    assert dry.duplicates == 0
    assert not save_dir.exists()


def test_cli_library_harvest_end_to_end_and_flag_guards(tmp_path):
    _seed_transcripts(tmp_path)
    save_dir = tmp_path / "memory"
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["LAST30DAYS_MEMORY_DIR"] = str(save_dir)

    res = subprocess.run(
        [
            sys.executable,
            str(CLI_SCRIPT),
            "library",
            "harvest",
            "--source",
            "claude",
            "--save-dir",
            str(save_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "Saved: 2" in res.stdout
    assert (save_dir / "finished" / "anthropic-2026-11-21.md").is_file()
    assert (save_dir / "finished" / "nvidia-2026-11-09.md").is_file()

    # Orphaned --dry-run outside library harvest must fail loudly with exit 2.
    bad = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "AI agents", "--dry-run", "--mock"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert bad.returncode == 2
    assert "library harvest" in bad.stderr

    # Malformed --since exits 2.
    bad_since = subprocess.run(
        [sys.executable, str(CLI_SCRIPT), "library", "harvest", "--since", "not-a-date"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
        check=False,
    )
    assert bad_since.returncode == 2
