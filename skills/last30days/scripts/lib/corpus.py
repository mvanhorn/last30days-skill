"""Deterministic, local-only document corpus source.

The corpus adapter deliberately has no HTTP dependency. It scans explicitly
registered directories, extracts small text documents (and PDFs only when the
local ``pdftotext`` binary is available), and returns normalized ``SourceItem``
objects for the shared relevance/fusion pipeline.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from shutil import which
from typing import Any, Iterable

from . import entity_extract, log, relevance, schema

SOURCE = "corpus"
SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf"}
IGNORED_DIRECTORIES = {".git", "node_modules"}
MAX_FILES = 500
MAX_TEXT_CHARS = 1_000_000
MAX_CACHE_ENTRIES = 2_000
CACHE_FILENAME = "corpus-cache.json"
CACHE_SCHEMA_VERSION = "last30days-corpus-cache/v1"

_CACHE_LOCK = threading.Lock()


@dataclass
class CorpusScanResult:
    """One bounded scan, including non-fatal extraction notes."""

    items: list[schema.SourceItem]
    notes: list[str] = field(default_factory=list)
    files_scanned: int = 0
    cache_hits: int = 0


def resolve_directories(
    cli_directories: Iterable[str] | None,
    configured: str | Iterable[str] | None,
) -> list[Path]:
    """Merge repeatable CLI paths with ``os.pathsep``-separated config paths."""
    raw: list[str] = [str(value) for value in (cli_directories or []) if str(value).strip()]
    if isinstance(configured, str):
        raw.extend(value for value in configured.split(os.pathsep) if value.strip())
    elif configured:
        raw.extend(str(value) for value in configured if str(value).strip())

    resolved: list[Path] = []
    seen: set[str] = set()
    for value in raw:
        path = Path(value.strip()).expanduser().resolve()
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def search(
    topic: str,
    directories: Iterable[Path | str],
    *,
    from_date: str,
    to_date: str,
    all_time: bool = False,
    limit: int = 12,
    cache_dir: Path | None = None,
) -> CorpusScanResult:
    """Search registered directories without making any network calls."""
    roots = resolve_directories([str(path) for path in directories], None)
    notes: list[str] = []
    cache_path = cache_dir / CACHE_FILENAME if cache_dir is not None else None
    with _CACHE_LOCK:
        cache = _load_cache(cache_path)

    candidates: list[tuple[float, int, schema.SourceItem]] = []
    seen_files: set[str] = set()
    files_scanned = 0
    cache_hits = 0
    pdf_available = which("pdftotext")
    pdf_unavailable_noted = False

    for root in roots:
        if not root.is_dir():
            notes.append(f"Skipped {root}: not a readable directory")
            continue
        for path in _iter_files(root):
            if files_scanned >= MAX_FILES:
                notes.append(f"Stopped after the {MAX_FILES}-file corpus scan limit")
                break
            key = os.path.normcase(str(path))
            if key in seen_files:
                continue
            seen_files.add(key)
            files_scanned += 1

            try:
                stat = path.stat()
            except OSError as exc:
                notes.append(f"Skipped {path}: {exc}")
                continue
            published_at = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).date().isoformat()
            if not all_time and not (from_date <= published_at <= to_date):
                continue

            cached = cache.get("entries", {}).get(str(path))
            if (
                isinstance(cached, dict)
                and cached.get("mtime_ns") == stat.st_mtime_ns
                and cached.get("size") == stat.st_size
                and isinstance(cached.get("text"), str)
            ):
                text = cached["text"]
                cache_hits += 1
            else:
                if path.suffix.lower() == ".pdf" and not pdf_available:
                    if not pdf_unavailable_noted:
                        notes.append("Skipped PDF files because pdftotext is not on PATH")
                        pdf_unavailable_noted = True
                    continue
                try:
                    text = _extract_text(path, pdftotext=pdf_available)
                except (OSError, subprocess.SubprocessError) as exc:
                    notes.append(f"Skipped {path}: {exc}")
                    continue
                cache.setdefault("entries", {})[str(path)] = {
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                    "text": text,
                }

            title = _path_title(path)
            score = _match_score(topic, f"{title}\n{text}")
            if score < 0.15:
                continue
            relative_path = str(path.relative_to(root))
            item = schema.SourceItem(
                item_id=f"C{hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]}",
                source=SOURCE,
                title=title,
                body=text,
                url="",
                container=str(path.parent),
                published_at=published_at,
                date_confidence="high",
                relevance_hint=score,
                why_relevant=f"Matched local file {relative_path}",
                snippet=text[:400].strip(),
                metadata={
                    "path": str(path),
                    "relative_path": relative_path,
                    "extension": path.suffix.lower(),
                    "local_only": True,
                },
            )
            candidates.append((score, stat.st_mtime_ns, item))
        if files_scanned >= MAX_FILES:
            break

    cache["schema_version"] = CACHE_SCHEMA_VERSION
    cache["entries"] = _bounded_entries(cache.get("entries", {}))
    with _CACHE_LOCK:
        _write_cache(cache_path, cache, notes)

    candidates.sort(key=lambda row: (-row[0], -row[1], row[2].title.casefold()))
    items = [item for _score, _mtime, item in candidates[: max(0, limit)]]
    log.source_log(
        "Corpus",
        f"scanned {files_scanned} file(s), {cache_hits} cache hit(s), {len(items)} match(es)",
        tty_only=False,
    )
    return CorpusScanResult(
        items=items,
        notes=notes,
        files_scanned=files_scanned,
        cache_hits=cache_hits,
    )


def _iter_files(root: Path) -> Iterable[Path]:
    candidates: list[Path] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES and not name.startswith(".")
        )
        current_path = Path(current)
        for name in sorted(file_names):
            if name.startswith("."):
                continue
            path = current_path / name
            if path.suffix.lower() in SUPPORTED_SUFFIXES and not path.is_symlink():
                candidates.append(path)
    # The extraction budget should land on the files most likely to survive the
    # normal recency window, not whichever 500 filenames sort first.
    candidates.sort(key=lambda path: (-_safe_mtime_ns(path), str(path).casefold()))
    yield from candidates


def _safe_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _extract_text(path: Path, *, pdftotext: str | None) -> str:
    if path.suffix.lower() == ".pdf":
        if not pdftotext:
            return ""
        completed = subprocess.run(
            [pdftotext, str(path), "-"],
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
        return completed.stdout[:MAX_TEXT_CHARS]
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(MAX_TEXT_CHARS)


def _path_title(path: Path) -> str:
    title = path.stem.replace("_", " ").replace("-", " ")
    return " ".join(title.split()) or path.name


def _match_score(topic: str, text: str) -> float:
    lexical = relevance.token_overlap_relevance(topic, text)
    topic_entities = entity_extract.extract_text_entities(topic)
    text_entities = entity_extract.extract_text_entities(text)
    entity_score = entity_extract.entity_overlap(topic_entities, text_entities)
    return round(max(lexical, entity_score * 0.9), 4)


def _load_cache(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        return {"schema_version": CACHE_SCHEMA_VERSION, "entries": {}}
    if not isinstance(payload.get("entries"), dict):
        payload["entries"] = {}
    return payload


def _bounded_entries(entries: Any) -> dict[str, Any]:
    if not isinstance(entries, dict):
        return {}
    ordered = sorted(
        (
            (path, value)
            for path, value in entries.items()
            if isinstance(path, str) and isinstance(value, dict)
        ),
        key=lambda row: int(row[1].get("mtime_ns") or 0),
        reverse=True,
    )
    return dict(ordered[:MAX_CACHE_ENTRIES])


def _write_cache(path: Path | None, payload: dict[str, Any], notes: list[str]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
    except OSError as exc:
        notes.append(f"Corpus cache unavailable: {exc}")
