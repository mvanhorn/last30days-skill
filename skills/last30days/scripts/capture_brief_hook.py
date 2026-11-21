#!/usr/bin/env python3
"""Silent post-turn lifecycle hook that archives completed last30days chat briefs.

Designed to run across local agent harnesses (Claude Code, Antigravity, Gemini
CLI, Codex CLI, Cursor, and Grok Build CLI) with zero chat output and negligible
latency on non-brief turns. Only Python standard library modules are imported on
startup; engine modules load lazily when a completed brief is detected.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path


_MAX_STDIN_BYTES = 4 * 1024 * 1024
_MAX_TRANSCRIPT_TAIL_BYTES = 256 * 1024
_MAX_JSON_TRANSCRIPT_BYTES = 4 * 1024 * 1024

_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})
_ABORTED_STATUSES = frozenset({"aborted", "error", "cancelled", "canceled"})
_ABORTED_TERMINATION_REASONS = frozenset(
    {
        "USER_CANCELED",
        "ERROR",
        "MAX_INVOCATIONS",
        "MAX_FORCED_INVOCATIONS",
        "MAX_TOKEN_BUDGET_EXCEEDED",
    }
)


def _debug_enabled() -> bool:
    return os.environ.get("LAST30DAYS_HOOK_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _debug_log(message: str) -> None:
    if _debug_enabled():
        sys.stderr.write(f"[capture_brief_hook] {message}\n")


def _auto_capture_disabled() -> bool:
    return os.environ.get("LAST30DAYS_AUTO_CAPTURE", "").strip().lower() in _DISABLED_VALUES


def _read_stdin_payload() -> dict[str, object] | None:
    raw = sys.stdin.read(_MAX_STDIN_BYTES + 1)
    if not raw or len(raw) > _MAX_STDIN_BYTES:
        return None
    data = json.loads(raw)
    return data if isinstance(data, dict) else None


def _should_skip_payload(payload: dict[str, object]) -> bool:
    if payload.get("stop_hook_active") is True:
        return True
    if payload.get("continuationActive") is True:
        return True
    status = str(payload.get("status") or "").strip().lower()
    if status in _ABORTED_STATUSES:
        return True
    reason = str(payload.get("terminationReason") or "").strip().upper()
    if reason in _ABORTED_TERMINATION_REASONS:
        return True
    return False


def _extract_inline_text(payload: dict[str, object]) -> str:
    for key in ("last_assistant_message", "prompt_response", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _resolve_transcript_path(payload: dict[str, object]) -> Path | None:
    raw_path = payload.get("transcript_path") or payload.get("transcriptPath")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path).expanduser()
    # Antigravity compact transcripts truncate fields >4 KB; prefer sibling full log.
    if path.name == "transcript.jsonl":
        full_sibling = path.with_name("transcript_full.jsonl")
        if full_sibling.is_file():
            return full_sibling
    return path if path.is_file() else None


def _read_tail_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()  # discard partial leading line
        chunk = handle.read(max_bytes)
    return chunk.decode("utf-8", errors="replace")


def _assistant_text_from_jsonl_record(record: dict[str, object]) -> str:
    record_type = record.get("type")

    # Antigravity transcript.jsonl / transcript_full.jsonl
    if record_type == "PLANNER_RESPONSE":
        content = record.get("content")
        return content if isinstance(content, str) else ""

    # Claude Code transcript JSONL
    if record_type == "assistant":
        message = record.get("message")
        if isinstance(message, dict):
            return _flatten_content(message.get("content"))

    # Codex CLI rollout JSONL
    if record_type == "response_item":
        pl = record.get("payload")
        if isinstance(pl, dict) and pl.get("role") == "assistant":
            return _flatten_content(pl.get("content"))
    if record_type == "event_msg":
        pl = record.get("payload")
        if isinstance(pl, dict) and pl.get("type") == "agent_message":
            msg = pl.get("message")
            return msg if isinstance(msg, str) else ""

    # Generic role-based JSONL fallback
    if record.get("role") in {"assistant", "model"}:
        return _flatten_content(record.get("content") or record.get("text"))
    return ""


def _flatten_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {None, "text", "output_text"}:
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        return "\n".join(parts)
    return ""


def _extract_transcript_text(path: Path) -> str:
    if path.suffix.lower() == ".json":
        if path.stat().st_size > _MAX_JSON_TRANSCRIPT_BYTES:
            return ""
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        messages = data.get("messages") if isinstance(data, dict) else None
        if isinstance(messages, list):
            for entry in reversed(messages):
                if not isinstance(entry, dict):
                    continue
                role = str(entry.get("type") or entry.get("role") or "").lower()
                if role in {"gemini", "model", "assistant"}:
                    text = _flatten_content(entry.get("content") or entry.get("text"))
                    if text.strip():
                        return text
        return ""

    tail = _read_tail_text(path, _MAX_TRANSCRIPT_TAIL_BYTES)
    for line in reversed(tail.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        text = _assistant_text_from_jsonl_record(record)
        if text.strip():
            return text
    return ""


def _fallback_date_from_payload(payload: dict[str, object]) -> date | None:
    raw = payload.get("timestamp")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _run_capture() -> None:
    if _auto_capture_disabled():
        return
    payload = _read_stdin_payload()
    if payload is None or _should_skip_payload(payload):
        return

    text = _extract_inline_text(payload)
    if not text:
        transcript = _resolve_transcript_path(payload)
        if transcript is not None:
            text = _extract_transcript_text(transcript)

    if not text or "All agents reported back!" not in text or "last30days" not in text:
        return

    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    from lib import brief_capture, env

    save_dir = env.resolve_memory_dir()
    if not save_dir:
        return

    brief = brief_capture.extract_finished_brief(
        text,
        fallback_date=_fallback_date_from_payload(payload),
    )
    if brief is None:
        return

    outcome = brief_capture.save_finished_brief(brief, save_dir)
    if outcome.status == "saved":
        try:
            from lib import library_index

            save_root = Path(save_dir).expanduser().resolve()
            db_path = library_index.saved_report_index_path(save_root)
            library_index.index_entry(
                outcome.path,
                db_path=db_path,
                source_format="finished-brief",
            )
        except Exception as exc:
            _debug_log(f"index_entry skipped ({type(exc).__name__}: {exc})")


def main() -> int:
    try:
        _run_capture()
    except Exception as exc:
        _debug_log(f"{type(exc).__name__}: {exc}")
    sys.stdout.write("{}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
