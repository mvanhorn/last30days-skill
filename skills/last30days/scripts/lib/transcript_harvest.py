"""Offline transcript scanner that backfills completed last30days chat briefs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from . import brief_capture


HARVEST_SOURCES: tuple[str, ...] = (
    "claude",
    "codex",
    "antigravity",
    "gemini",
    "cursor",
)
VALID_SOURCE_FLAGS: tuple[str, ...] = ("auto", "all", *HARVEST_SOURCES)
_FOOTER_MARKER_BYTES = b"All agents reported back!"


@dataclass(frozen=True, slots=True)
class HarvestResult:
    """Summary of an offline transcript backfill pass."""

    scanned_files: int
    matched_briefs: int
    saved: int
    duplicates: int
    saved_paths: tuple[Path, ...]
    notes: tuple[str, ...]
    dry_run: bool


def _home_dir(home: Path | str | None) -> Path:
    return Path(home).expanduser() if home is not None else Path.home()


def _discover_source_files(source: str, home: Path) -> list[Path]:
    if source == "claude":
        root = home / ".claude" / "projects"
        return sorted(root.rglob("*.jsonl")) if root.is_dir() else []
    if source == "codex":
        root = home / ".codex" / "sessions"
        return sorted(root.rglob("*.jsonl")) if root.is_dir() else []
    if source == "antigravity":
        seen_dirs: set[Path] = set()
        found: list[Path] = []
        roots = [
            home / ".t3" / "userdata" / "providers" / "antigravity",
            home / ".gemini",
        ]
        for root in roots:
            if not root.is_dir():
                continue
            for full_log in sorted(root.rglob("transcript_full.jsonl")):
                seen_dirs.add(full_log.parent)
                found.append(full_log)
            for compact_log in sorted(root.rglob("transcript.jsonl")):
                if compact_log.parent not in seen_dirs:
                    found.append(compact_log)
        return found
    if source == "gemini":
        root = home / ".gemini" / "tmp"
        return sorted(root.rglob("session-*.json")) if root.is_dir() else []
    if source == "cursor":
        root = home / ".cursor" / "transcripts"
        return sorted(root.rglob("*.jsonl")) if root.is_dir() else []
    raise ValueError(f"unknown harvest source: {source}")


def _file_contains_marker(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            overlap = b""
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    return False
                if _FOOTER_MARKER_BYTES in overlap + chunk:
                    return True
                overlap = chunk[-len(_FOOTER_MARKER_BYTES) :]
    except OSError:
        return False


def _flatten_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {None, "text", "output_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _iter_assistant_texts(source: str, path: Path) -> Iterator[tuple[str, date | None]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        messages = data.get("messages") if isinstance(data, dict) else None
        if isinstance(messages, list):
            for entry in messages:
                if not isinstance(entry, dict):
                    continue
                role = str(entry.get("type") or entry.get("role") or "").lower()
                if role in {"gemini", "model", "assistant"}:
                    text = _flatten_content(entry.get("content") or entry.get("text"))
                    if text:
                        yield text, _parse_iso_date(entry.get("timestamp"))
        return

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "All agents reported back!" not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue

            fallback = _parse_iso_date(record.get("timestamp") or record.get("created_at"))
            rec_type = record.get("type")
            text = ""
            if source == "claude" and rec_type == "assistant":
                msg = record.get("message")
                if isinstance(msg, dict):
                    text = _flatten_content(msg.get("content"))
            elif source == "codex":
                pl = record.get("payload")
                if rec_type == "response_item" and isinstance(pl, dict) and pl.get("role") == "assistant":
                    text = _flatten_content(pl.get("content"))
                elif rec_type == "event_msg" and isinstance(pl, dict) and pl.get("type") == "agent_message":
                    msg_text = pl.get("message")
                    text = msg_text if isinstance(msg_text, str) else ""
            elif source == "antigravity" and rec_type == "PLANNER_RESPONSE":
                content = record.get("content")
                text = content if isinstance(content, str) else ""
            elif record.get("role") in {"assistant", "model"}:
                text = _flatten_content(record.get("content") or record.get("text"))

            if text:
                yield text, fallback


def _existing_hashes_for(save_dir: Path, slug: str, published_date: date) -> set[str]:
    finished_dir = save_dir / brief_capture.FINISHED_SUBDIR
    if not finished_dir.is_dir():
        return set()
    prefix = f"{slug}-{published_date.isoformat()}"
    hashes: set[str] = set()
    for path in finished_dir.glob(f"{prefix}*.md"):
        try:
            hashes.add(brief_capture.content_hash(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return hashes


def harvest_transcripts(
    *,
    save_dir: Path | str,
    source: str = "auto",
    since: date | None = None,
    dry_run: bool = False,
    home: Path | str | None = None,
) -> HarvestResult:
    """Scan local harness transcripts offline and archive finished briefs."""
    normalized = source.strip().lower() or "auto"
    if normalized not in VALID_SOURCE_FLAGS:
        raise ValueError(
            f"unsupported --source '{source}'; expected one of {', '.join(VALID_SOURCE_FLAGS)}"
        )

    selected_sources = HARVEST_SOURCES if normalized in {"auto", "all"} else (normalized,)
    base_home = _home_dir(home)
    target_dir = Path(save_dir).expanduser()

    scanned_files = 0
    matched_briefs = 0
    saved = 0
    duplicates = 0
    saved_paths: list[Path] = []
    notes: list[str] = []
    dry_run_seen_hashes: set[str] = set()

    for src in selected_sources:
        files = _discover_source_files(src, base_home)
        for path in files:
            scanned_files += 1
            try:
                mtime_date = datetime.fromtimestamp(path.stat().st_mtime).date()
            except OSError as exc:
                notes.append(f"Skipped {path}: {exc}")
                continue
            if since is not None and mtime_date < since:
                continue
            if not _file_contains_marker(path):
                continue

            try:
                candidates = list(_iter_assistant_texts(src, path))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                notes.append(f"Skipped {path}: {exc}")
                continue

            for text, fallback_date in candidates:
                brief = brief_capture.extract_finished_brief(
                    text,
                    fallback_date=fallback_date or mtime_date,
                )
                if brief is None:
                    continue
                if since is not None and brief.published_date < since:
                    continue
                matched_briefs += 1
                if dry_run:
                    existing_hashes = _existing_hashes_for(
                        target_dir, brief.slug, brief.published_date
                    )
                    if (
                        brief.content_hash in existing_hashes
                        or brief.content_hash in dry_run_seen_hashes
                    ):
                        duplicates += 1
                    else:
                        dry_run_seen_hashes.add(brief.content_hash)
                        saved += 1
                else:
                    outcome = brief_capture.save_finished_brief(brief, target_dir)
                    if outcome.status == "saved":
                        saved += 1
                        saved_paths.append(outcome.path)
                    else:
                        duplicates += 1

    return HarvestResult(
        scanned_files=scanned_files,
        matched_briefs=matched_briefs,
        saved=saved,
        duplicates=duplicates,
        saved_paths=tuple(saved_paths),
        notes=tuple(notes),
        dry_run=dry_run,
    )
