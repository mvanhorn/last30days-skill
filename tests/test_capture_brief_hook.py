"""Subprocess contract tests for the silent multi-harness brief capture hook."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = REPO_ROOT / "skills" / "last30days" / "scripts" / "capture_brief_hook.py"

SAMPLE_BRIEF = """🌐 last30days · Anthropic · Oct 22 to Nov 21, 2026 · v4.5.0

## What I learned

Anthropic developer threads focus on tool reliability and agent workflows.

```text
---
✅ All agents reported back!
├─ 🟠 Reddit: 12 threads │ 48 comments
└─ 🗓️ Oct 22 to Nov 21, 2026
---
```

---
I'm now an expert on Anthropic. Want to explore pricing or competitors? Just ask.
"""


def _run_hook(
    payload: str | dict[str, object],
    *,
    save_dir: Path | str | None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("LAST30DAYS_AUTO_CAPTURE", None)
    env.pop("LAST30DAYS_HOOK_DEBUG", None)
    if save_dir is not None:
        env["LAST30DAYS_MEMORY_DIR"] = str(save_dir)
    if extra_env:
        env.update(extra_env)
    stdin_data = json.dumps(payload) if isinstance(payload, dict) else payload
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=stdin_data,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("harness", "payload"),
    [
        (
            "claude",
            {
                "hook_event_name": "Stop",
                "session_id": "s-1",
                "stop_hook_active": False,
                "last_assistant_message": SAMPLE_BRIEF,
            },
        ),
        (
            "codex",
            {
                "hook_event_name": "Stop",
                "turn_id": "t-1",
                "stop_hook_active": False,
                "last_assistant_message": SAMPLE_BRIEF,
            },
        ),
        (
            "gemini",
            {
                "hook_event_name": "AfterAgent",
                "session_id": "g-1",
                "stop_hook_active": False,
                "prompt_response": SAMPLE_BRIEF,
            },
        ),
        (
            "cursor",
            {
                "hook_event_name": "afterAgentResponse",
                "conversation_id": "c-1",
                "text": SAMPLE_BRIEF,
            },
        ),
        (
            "grok",
            {
                "hookEventName": "Stop",
                "sessionId": "x-1",
                "last_assistant_message": SAMPLE_BRIEF,
            },
        ),
    ],
)
def test_hook_captures_finished_brief_across_inline_harnesses(tmp_path, harness, payload):
    result = _run_hook(payload, save_dir=tmp_path)

    assert result.returncode == 0, f"{harness}: {result.stderr}"
    assert result.stdout.strip() == "{}"
    assert result.stderr == ""

    saved = tmp_path / "finished" / "anthropic-2026-11-21.md"
    assert saved.is_file(), f"{harness} did not write brief"
    text = saved.read_text(encoding="utf-8")
    assert text.startswith("🌐 last30days · Anthropic · Oct 22 to Nov 21, 2026 · v4.5.0")
    assert "I'm now an expert" not in text


def test_hook_resolves_antigravity_untruncated_transcript_full(tmp_path):
    logs_dir = tmp_path / "brain" / "conv-1" / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    compact = logs_dir / "transcript.jsonl"
    full = logs_dir / "transcript_full.jsonl"

    # Compact transcript truncates content before the closing footer.
    compact.write_text(
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "step_index": 7,
                "content": SAMPLE_BRIEF[:120],
                "truncated_fields": ["content"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    full.write_text(
        json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "step_index": 7,
                "content": SAMPLE_BRIEF,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    save_dir = tmp_path / "memory"
    payload = {
        "hookEventName": "Stop",
        "conversationId": "conv-1",
        "transcriptPath": str(compact),
        "terminationReason": "NO_TOOL_CALL",
        "continuationActive": False,
    }
    result = _run_hook(payload, save_dir=save_dir)

    assert result.returncode == 0
    assert result.stdout.strip() == "{}"
    assert result.stderr == ""
    saved = save_dir / "finished" / "anthropic-2026-11-21.md"
    assert saved.is_file()
    assert "All agents reported back!" in saved.read_text(encoding="utf-8")


def test_hook_reads_claude_transcript_fallback_when_inline_missing(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": SAMPLE_BRIEF}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    save_dir = tmp_path / "memory"
    result = _run_hook(
        {"hook_event_name": "Stop", "transcript_path": str(transcript)},
        save_dir=save_dir,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "{}"
    assert (save_dir / "finished" / "anthropic-2026-11-21.md").is_file()


@pytest.mark.parametrize(
    ("payload", "save_dir_override", "extra_env"),
    [
        ({"hook_event_name": "Stop", "last_assistant_message": "Fixed the unit test."}, None, None),
        ({"hook_event_name": "Stop", "stop_hook_active": True, "last_assistant_message": SAMPLE_BRIEF}, None, None),
        ({"hookEventName": "Stop", "continuationActive": True, "last_assistant_message": SAMPLE_BRIEF}, None, None),
        ({"hookEventName": "Stop", "terminationReason": "USER_CANCELED", "last_assistant_message": SAMPLE_BRIEF}, None, None),
        ({"hook_event_name": "stop", "status": "aborted", "text": SAMPLE_BRIEF}, None, None),
        ({"hook_event_name": "Stop", "last_assistant_message": SAMPLE_BRIEF}, "", None),
        ({"hook_event_name": "Stop", "last_assistant_message": SAMPLE_BRIEF}, None, {"LAST30DAYS_AUTO_CAPTURE": "0"}),
    ],
)
def test_hook_fast_silent_noop_on_guards_and_disabled_config(
    tmp_path, payload, save_dir_override, extra_env
):
    target_dir = tmp_path / "should_not_exist"
    effective_save_dir = save_dir_override if save_dir_override is not None else target_dir
    result = _run_hook(payload, save_dir=effective_save_dir, extra_env=extra_env)

    assert result.returncode == 0
    assert result.stdout.strip() == "{}"
    assert result.stderr == ""
    assert not target_dir.exists()


def test_hook_swallows_errors_silently_unless_debug_enabled(tmp_path):
    # Malformed stdin JSON
    bad_json = _run_hook("{not valid json", save_dir=tmp_path)
    assert bad_json.returncode == 0
    assert bad_json.stdout.strip() == "{}"
    assert bad_json.stderr == ""

    # Empty stdin
    empty = _run_hook("", save_dir=tmp_path)
    assert empty.returncode == 0
    assert empty.stdout.strip() == "{}"
    assert empty.stderr == ""

    # Missing transcript path
    missing = _run_hook(
        {"hook_event_name": "Stop", "transcript_path": str(tmp_path / "missing.jsonl")},
        save_dir=tmp_path,
    )
    assert missing.returncode == 0
    assert missing.stdout.strip() == "{}"
    assert missing.stderr == ""

    # Unwritable save path: silent by default, logs only when debug enabled.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file", encoding="utf-8")
    silent_fail = _run_hook(
        {"hook_event_name": "Stop", "last_assistant_message": SAMPLE_BRIEF},
        save_dir=blocker,
    )
    assert silent_fail.returncode == 0
    assert silent_fail.stdout.strip() == "{}"
    assert silent_fail.stderr == ""

    debug_fail = _run_hook(
        {"hook_event_name": "Stop", "last_assistant_message": SAMPLE_BRIEF},
        save_dir=blocker,
        extra_env={"LAST30DAYS_HOOK_DEBUG": "1"},
    )
    assert debug_fail.returncode == 0
    assert debug_fail.stdout.strip() == "{}"
    assert "capture_brief_hook" in debug_fail.stderr
